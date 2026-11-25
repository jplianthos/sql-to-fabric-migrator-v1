SELECT
    CONVERT(nvarchar(128), SERVERPROPERTY('ProductVersion')) AS engineVersion,
    CONVERT(int, SERVERPROPERTY('ProductMajorVersion')) AS engineMajor,
    CONVERT(nvarchar(128), SERVERPROPERTY('Edition')) AS edition,
    CONVERT(nvarchar(128), SERVERPROPERTY('ProductLevel')) AS productLevel,
    SUSER_SNAME() AS sourceUser,
    ORIGINAL_LOGIN() AS originalUser,
    IS_SRVROLEMEMBER('sysadmin') AS isSysadmin,
    net_transport AS transport,
    auth_scheme AS authentication,
    encrypt_option AS encrypted
FROM sys.dm_exec_connections
WHERE session_id = @@SPID;
