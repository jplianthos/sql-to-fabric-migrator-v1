import hashlib
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

import pyarrow as pa
import pyarrow.parquet as pq

from .safety import SafetyError, canonical, digest


MAX_BYTES = 64_000_000
MAX_ROWS = 100000
COLUMN_KEYS = {
    "ordinal", "name", "typeName", "maxLength", "precision", "scale",
    "nullable", "computedColumn", "primaryKeyOrdinal", "userDefined", "assemblyType",
}
INTEGER_TYPES = {
    "tinyint": (0, 255, pa.int16()), "smallint": (-32768, 32767, pa.int16()),
    "int": (-(2**31), 2**31 - 1, pa.int32()), "bigint": (-(2**63), 2**63 - 1, pa.int64()),
}


def arrow_type(column, precision):
    kind = column["typeName"]
    if kind in INTEGER_TYPES:
        return INTEGER_TYPES[kind][2]
    if kind == "bit":
        return pa.bool_()
    if kind in ("decimal", "numeric") and 1 <= column["precision"] <= 38 and 0 <= column["scale"] <= column["precision"]:
        return pa.decimal128(column["precision"], column["scale"])
    if kind in ("float", "real") and column["precision"] in (24, 53):
        return pa.float32() if column["precision"] == 24 else pa.float64()
    if kind in ("char", "varchar", "nchar", "nvarchar") and 1 <= column["maxLength"] <= 8000:
        return pa.string()
    if kind in ("binary", "varbinary") and 1 <= column["maxLength"] <= 8000:
        return pa.binary()
    if kind == "date":
        return pa.date32()
    if kind == "datetime2" and 0 <= column["scale"] <= precision:
        return pa.timestamp("us" if precision == 6 else "ms")
    raise SafetyError(
        "Unsupported or potentially lossy source type in this bronze checkpoint. "
        "LOBs, aliases, computed columns, datetime/time/offset, datetime2 beyond E2, GUID/CLR and custom maps are blocked."
    )


def validate_columns(columns, precision, normalization):
    if not isinstance(columns, list) or not 1 <= len(columns) <= 128:
        raise SafetyError("Select one visible user table with 1-128 supported columns.")
    names, keys = [], {}
    previous_ordinal = 0
    for column in columns:
        if (not isinstance(column, dict) or set(column) != COLUMN_KEYS
                or any(type(column[key]) is not int for key in ("ordinal", "maxLength", "precision", "scale", "primaryKeyOrdinal"))
                or not previous_ordinal < column["ordinal"] <= 2147483647 or not isinstance(column["name"], str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", column["name"]) is None
                or not isinstance(column["typeName"], str)
                or any(type(column[key]) is not bool for key in ("nullable", "computedColumn", "userDefined", "assemblyType"))
                or column["computedColumn"] or column["userDefined"] or column["assemblyType"]
                or not 0 <= column["primaryKeyOrdinal"] <= 16):
            raise SafetyError("Column metadata is malformed, computed, or outside this checkpoint's ASCII column-name contract.")
        previous_ordinal = column["ordinal"]
        if column["primaryKeyOrdinal"]:
            if column["nullable"] or column["primaryKeyOrdinal"] in keys:
                raise SafetyError("A complete, nonnullable primary key is required for deterministic extraction.")
            keys[column["primaryKeyOrdinal"]] = column["name"]
        names.append(column["name"])
        arrow_type(column, precision)
        if column["typeName"] in ("decimal", "numeric") and normalization["decimalScale"] < column["scale"]:
            raise SafetyError("F2 decimalScale would discard source precision; choose a lossless explicit comparison scale.")
    if len(set(name.casefold() for name in names)) != len(names):
        raise SafetyError("Case-colliding column names are not supported by this bronze route.")
    if not keys or sorted(keys) != list(range(1, len(keys) + 1)):
        raise SafetyError("A complete visible primary key is required; no arbitrary sample order is inferred.")
    return [keys[index] for index in sorted(keys)]


def normalized(value, column, settings):
    if value is None:
        if not column["nullable"]:
            raise SafetyError("A nonnullable column returned NULL.")
        return ["null"]
    kind = column["typeName"]
    if kind in INTEGER_TYPES:
        lower, upper, _ = INTEGER_TYPES[kind]
        if type(value) is not int or not lower <= value <= upper:
            raise SafetyError("An integer value does not match the selected source schema.")
        return ["integer", value]
    if kind == "bit":
        if value not in (0, 1) or not isinstance(value, (bool, int)):
            raise SafetyError("A bit value does not match the selected source schema.")
        return ["boolean", bool(value)]
    if kind in ("decimal", "numeric"):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise SafetyError("A decimal value is nonfinite or was implicitly converted.")
        with localcontext() as context:
            context.prec = 80
            try:
                scaled = value.quantize(Decimal(1).scaleb(-settings["decimalScale"]))
            except InvalidOperation:
                raise SafetyError("A decimal value exceeds the explicit normalization contract.") from None
            if scaled != value:
                raise SafetyError("Decimal normalization would silently lose precision.")
        return ["decimal", format(scaled.copy_abs() if scaled == 0 else scaled, "f")]
    if kind in ("float", "real"):
        if type(value) is not float or not math.isfinite(value):
            raise SafetyError("A floating-point value is nonfinite or has an unexpected type.")
        return ["float", value.hex()]
    if kind in ("char", "varchar", "nchar", "nvarchar"):
        if not isinstance(value, str):
            raise SafetyError("A text value has an unexpected type.")
        try:
            encoded = value.encode("utf-16-le" if kind in ("nchar", "nvarchar") else "utf-8")
            value.encode("utf-8")
        except UnicodeError:
            raise SafetyError("Text cannot be represented losslessly as UTF-8.") from None
        if kind in ("nchar", "nvarchar") and len(encoded) > column["maxLength"]:
            raise SafetyError("Unicode text exceeds the explicit source column length.")
        return ["text", value.rstrip(" ") if settings["trimTrailingSpaces"] else value]
    if kind in ("binary", "varbinary"):
        if not isinstance(value, bytes) or len(value) > column["maxLength"]:
            raise SafetyError("A binary value has an unexpected type.")
        return ["binary", value.hex()]
    if kind == "date":
        if type(value) is not date:
            raise SafetyError("A date value has an unexpected type.")
        return ["date", value.isoformat()]
    if kind == "datetime2":
        if type(value) is not datetime or value.tzinfo is not None:
            raise SafetyError("A datetime2 value must be a timezone-naive SQL timestamp.")
        if value.microsecond % (10 ** (6 - column["scale"])):
            raise SafetyError("A timestamp exceeds the explicit source precision; no truncation was accepted.")
        if settings["datetimePrecision"] == "milliseconds":
            value = value.replace(microsecond=(value.microsecond // 1000) * 1000)
        return ["datetime2", value.isoformat(timespec="microseconds")]
    raise SafetyError("No verified comparison rule exists for this type.")


def signature(rows, columns, settings):
    names = [column["name"] for column in columns]
    if len(rows) > MAX_ROWS or any(set(row) != set(names) for row in rows):
        raise SafetyError("Rows differ from the explicit projection or exceed the bounded row limit.")
    values = [[normalized(row[column["name"]], column, settings) for column in columns] for row in rows]
    row_hashes = sorted(digest(row) for row in values)
    profiles = []
    for index, column in enumerate(columns):
        cells = [row[index] for row in values]
        nonnull = [cell for cell in cells if cell != ["null"]]
        profiles.append({
            "name": column["name"], "nulls": len(cells) - len(nonnull),
            "distinctValues": len({canonical(cell) for cell in nonnull}),
            "maxUtf8Bytes": max((len(cell[1].encode("utf-8")) for cell in nonnull), default=0)
            if column["typeName"] in ("char", "varchar", "nchar", "nvarchar") else None,
        })
    return {
        "rows": len(rows), "profiles": profiles,
        "rowHash": hashlib.sha256("".join(row_hashes).encode("ascii")).hexdigest(),
    }


def check_keys(rows, keys):
    seen = set()
    for row in rows:
        value = tuple(row[name] for name in keys)
        if None in value or value in seen:
            raise SafetyError("The relied-on primary key contains NULLs or duplicates; no successful migration is recorded.")
        seen.add(value)


def encode(rows, columns, precision):
    try:
        schema = pa.schema([
            pa.field(column["name"], arrow_type(column, precision), nullable=bool(column["nullable"]))
            for column in columns
        ])
        table = pa.Table.from_pylist(rows, schema=schema)
        if table.nbytes > MAX_BYTES:
            raise SafetyError("The Arrow table exceeds the 64 MB canary budget.")
        output = pa.BufferOutputStream()
        pq.write_table(
            table, output, compression="snappy", version="2.6", coerce_timestamps="us" if precision == 6 else "ms",
            allow_truncated_timestamps=False, use_deprecated_int96_timestamps=False,
        )
        result = output.getvalue().to_pybytes()
    except (pa.ArrowException, ValueError, OverflowError):
        raise SafetyError("Parquet encoding refused the explicit types or a lossy conversion; no artifact was accepted.") from None
    if not 0 < len(result) <= MAX_BYTES:
        raise SafetyError("The Parquet file exceeds the 64 MB canary budget.")
    return result


def decode(content, columns, precision, max_rows):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise SafetyError("The exact Parquet artifact is missing or oversized.")
    try:
        reader = pq.ParquetFile(pa.BufferReader(content))
        expected = pa.schema([
            pa.field(column["name"], arrow_type(column, precision), nullable=bool(column["nullable"]))
            for column in columns
        ])
        if reader.metadata.num_rows > max_rows or not reader.schema_arrow.equals(expected, check_metadata=False):
            raise SafetyError("Parquet row count or explicit schema differs from the approved artifact.")
        table = reader.read()
        if table.nbytes > MAX_BYTES:
            raise SafetyError("Decoded Parquet exceeds the 64 MB canary budget.")
        return table.to_pylist()
    except (pa.ArrowException, ValueError, OverflowError):
        raise SafetyError("The exact Parquet artifact could not be decoded.") from None


def differences(expected, observed, checks):
    result = []
    for key in ("rows", "profiles", "rowHash"):
        if key == "profiles" and checks == "counts" or key == "rowHash" and checks != "counts-profiles-hashes":
            continue
        if expected[key] != observed[key]:
            result.append(key)
    return result
