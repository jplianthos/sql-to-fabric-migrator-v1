SELECT
    c.column_id AS ordinal,
    c.name,
    t.name AS typeName,
    c.max_length AS maxLength,
    c.precision,
    c.scale,
    c.is_nullable AS nullable,
    c.is_computed AS computedColumn,
    t.is_user_defined AS userDefined,
    t.is_assembly_type AS assemblyType,
    ISNULL(ic.key_ordinal, 0) AS primaryKeyOrdinal
FROM sys.columns AS c
INNER JOIN sys.types AS t ON c.user_type_id = t.user_type_id
INNER JOIN sys.objects AS o ON c.object_id = o.object_id
INNER JOIN sys.schemas AS s ON o.schema_id = s.schema_id
LEFT JOIN sys.indexes AS i ON i.object_id = o.object_id AND i.is_primary_key = 1
LEFT JOIN sys.index_columns AS ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.column_id = c.column_id
WHERE o.is_ms_shipped = 0 AND o.type = 'U' AND s.name = N'__s2f_schema__' AND o.name = N'__s2f_table__'
ORDER BY c.column_id;
