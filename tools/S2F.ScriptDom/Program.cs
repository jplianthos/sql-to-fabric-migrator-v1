using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.SqlServer.TransactSql.ScriptDom;

var input = Console.In.ReadToEnd();
try
{
    if (args.Length != 1 || args[0] is not ("--guard" or "--table") || input.Length > 65536)
        throw new InvalidDataException("Expected a bounded catalog or exact table scope.");
    using var scope = args[0] == "--table" ? JsonDocument.Parse(input) : null;
    var sql = scope is null ? input : scope.RootElement.GetProperty("sql").GetString()!;
    if (
        sql.Length is 0 or > 32768 || sql.Any(character => character > 127 || character == '\0'))
        throw new InvalidDataException("Expected a bounded ASCII catalog SELECT on standard input.");
    var fragment = new TSql130Parser(initialQuotedIdentifiers: true)
        .Parse(new StringReader(sql), out var errors);
    if (errors.Count != 0)
        throw new InvalidDataException("Source SQL failed SQL Server 2016 grammar validation.");
    if (fragment is not TSqlScript script || script.Batches.Count != 1 ||
        script.Batches[0].Statements.Count != 1 ||
        script.Batches[0].Statements[0] is not SelectStatement ||
        fragment.ScriptTokenStream.Any(token => token.TokenType is
            TSqlTokenType.Go or TSqlTokenType.SingleLineComment or TSqlTokenType.MultilineComment or TSqlTokenType.Collate))
        throw new InvalidDataException("Only one catalog SELECT without batches, comments or explicit collations is permitted.");
    if (scope is null)
    {
        fragment.Accept(new CatalogGuard());
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            contractVersion = "1.0", accepted = true,
            sha256 = Hash(sql)
        }));
    }
    else
    {
        TableGuard.Check((SelectStatement)script.Batches[0].Statements[0], scope.RootElement);
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            contractVersion = "1.0", accepted = true, sha256 = Hash(sql), scopeSha256 = Hash(input)
        }));
    }
    return 0;
}
catch (Exception error) when (error is InvalidDataException or JsonException or KeyNotFoundException or
    InvalidOperationException or NullReferenceException or FormatException)
{
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        contractVersion = "1.0", accepted = false,
        error = error is InvalidDataException ? error.Message : "Source SQL or scope is not admitted."
    }));
    return 1;
}

static string Hash(string value) =>
    Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(value))).ToLowerInvariant();

sealed class TableGuard : TSqlFragmentVisitor
{
    private static readonly HashSet<string> Nodes = new(StringComparer.Ordinal)
    {
        "SelectStatement", "QuerySpecification", "SelectScalarExpression",
        "ColumnReferenceExpression", "MultiPartIdentifier", "Identifier",
        "SchemaObjectName", "NamedTableReference", "FromClause", "OrderByClause",
        "ExpressionWithSortOrder", "TopRowFilter", "IntegerLiteral", "ParenthesisExpression"
    };

    public override void Visit(TSqlFragment node)
    {
        if (!Nodes.Contains(node.GetType().Name))
            throw new InvalidDataException("Table extraction admits only a projected, ordered SELECT.");
    }

    public static void Check(SelectStatement statement, JsonElement scope)
    {
        var columns = scope.GetProperty("columns").EnumerateArray().Select(value => value.GetString()!).ToArray();
        var keys = scope.GetProperty("keys").EnumerateArray().Select(value => value.GetString()!).ToArray();
        var schema = scope.GetProperty("schema").GetString();
        var table = scope.GetProperty("table").GetString();
        if (statement.Into is not null || statement.WithCtesAndXmlNamespaces is not null ||
            statement.OptimizerHints.Count != 0 ||
            columns.Length is 0 or > 128 || keys.Length is 0 or > 16 ||
            columns.Distinct(StringComparer.Ordinal).Count() != columns.Length ||
            keys.Distinct(StringComparer.Ordinal).Count() != keys.Length || keys.Any(key => !columns.Contains(key)) ||
            statement.QueryExpression is not QuerySpecification query ||
            query.UniqueRowFilter != UniqueRowFilter.NotSpecified || query.WhereClause is not null ||
            query.GroupByClause is not null || query.HavingClause is not null ||
            query.SelectElements.Count != columns.Length || query.FromClause?.TableReferences.Count != 1 ||
            query.FromClause.TableReferences[0] is not NamedTableReference from ||
            from.Alias is not null || from.TableHints.Count != 0 ||
            from.SchemaObject.Identifiers.Count != 2 ||
            from.SchemaObject.Identifiers[0].Value != schema || from.SchemaObject.Identifiers[1].Value != table)
            throw new InvalidDataException("The SELECT differs from the exact selected table scope.");
        for (var index = 0; index < columns.Length; index++)
            if (query.SelectElements[index] is not SelectScalarExpression selected || selected.ColumnName is not null ||
                !Column(selected.Expression, columns[index]))
                throw new InvalidDataException("The projected columns differ from the selected table scope.");
        if (query.OrderByClause?.OrderByElements.Count != keys.Length)
            throw new InvalidDataException("The primary-key ordering is required.");
        for (var index = 0; index < keys.Length; index++)
            if (!Column(query.OrderByClause.OrderByElements[index].Expression, keys[index]) ||
                query.OrderByClause.OrderByElements[index].SortOrder != SortOrder.Ascending)
                throw new InvalidDataException("The primary-key ordering differs from the selected scope.");
        var limit = scope.GetProperty("limit");
        var top = query.TopRowFilter?.Expression is ParenthesisExpression parenthesis
            ? parenthesis.Expression : query.TopRowFilter?.Expression;
        if (limit.ValueKind == JsonValueKind.Null)
        {
            if (query.TopRowFilter is not null)
                throw new InvalidDataException("Full extraction cannot silently sample.");
        }
        else if (!limit.TryGetInt32(out var count) || count is < 0 or > 100000 ||
            query.TopRowFilter is null || query.TopRowFilter.Percent || query.TopRowFilter.WithTies ||
            top is not IntegerLiteral literal ||
            literal.Value != count.ToString(System.Globalization.CultureInfo.InvariantCulture))
            throw new InvalidDataException("The sample limit differs from the explicit selection.");
        statement.Accept(new TableGuard());
    }

    private static bool Column(ScalarExpression expression, string expected) =>
        expression is ColumnReferenceExpression column && column.ColumnType == ColumnType.Regular &&
        column.MultiPartIdentifier?.Identifiers.Count == 1 &&
        column.MultiPartIdentifier.Identifiers[0].Value == expected;
}

sealed class CatalogGuard : TSqlFragmentVisitor
{
    private static readonly HashSet<string> Nodes = new(StringComparer.Ordinal)
    {
        "TSqlScript", "TSqlBatch", "SelectStatement", "QuerySpecification",
        "SelectScalarExpression", "ColumnReferenceExpression", "MultiPartIdentifier",
        "Identifier", "IdentifierOrValueExpression", "FunctionCall", "SchemaObjectName",
        "NamedTableReference", "QualifiedJoin", "JoinParenthesisTableReference",
        "FromClause", "WhereClause", "BooleanComparisonExpression", "BooleanBinaryExpression",
        "BooleanIsNullExpression", "InPredicate", "BooleanParenthesisExpression",
        "OrderByClause", "ExpressionWithSortOrder", "IntegerLiteral", "StringLiteral",
        "NullLiteral", "GlobalVariableExpression", "BinaryExpression",
        "SqlDataTypeReference", "ConvertCall", "CastCall"
    };
    private static readonly HashSet<string> Functions = new(StringComparer.OrdinalIgnoreCase)
    {
        "SERVERPROPERTY", "SUSER_SNAME", "ORIGINAL_LOGIN", "IS_SRVROLEMEMBER",
        "HAS_PERMS_BY_NAME", "DB_NAME", "QUOTENAME", "ISNULL"
    };
    private static readonly HashSet<string> Tables = new(StringComparer.OrdinalIgnoreCase)
    {
        "dm_exec_connections", "databases", "schemas", "objects", "columns", "types", "indexes", "index_columns"
    };

    public override void Visit(TSqlFragment node)
    {
        if (!Nodes.Contains(node.GetType().Name))
            throw new InvalidDataException("Source AST node is not admitted: " + node.GetType().Name + ".");
        if (node is SelectStatement select &&
            (select.Into is not null || select.WithCtesAndXmlNamespaces is not null ||
             select.OptimizerHints.Count != 0))
            throw new InvalidDataException("SELECT INTO, CTEs and hints are not admitted.");
        if (node is FunctionCall function &&
            (function.CallTarget is not null || !Functions.Contains(function.FunctionName.Value)))
            throw new InvalidDataException("Source function is not on the SQL2016 catalog allowlist.");
        if (node is NamedTableReference table &&
            (table.SchemaObject.Identifiers.Count != 2 ||
             !table.SchemaObject.Identifiers[0].Value.Equals("sys", StringComparison.OrdinalIgnoreCase) ||
             !Tables.Contains(table.SchemaObject.Identifiers[1].Value) || table.TableHints.Count != 0))
            throw new InvalidDataException("Only the fixed SQL2016 catalog surface is admitted.");
        if (node is GlobalVariableExpression variable &&
            !variable.Name.Equals("@@SPID", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Only the current source session variable is admitted.");
        if (node is ColumnReferenceExpression column && column.ColumnType != ColumnType.Regular)
            throw new InvalidDataException("Wildcard and pseudo-column source reads are not admitted.");
    }
}
