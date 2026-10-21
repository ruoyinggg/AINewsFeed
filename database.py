"""Small parameter/row adapter for optional hosted PostgreSQL storage."""
import re
import ssl


class Record(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def record_factory(cursor):
    names = [column.name for column in (cursor.description or [])]
    return lambda values: Record(zip(names, values))


class PostgresConnection:
    def __init__(self, url):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        options = conninfo_to_dict(url)
        # Local test databases may use plain connections; hosted databases verify TLS.
        if options.get('host') not in ('127.0.0.1', 'localhost', '::1'):
            options['sslmode'] = 'verify-full'
            options.setdefault('sslrootcert', ssl.get_default_verify_paths().cafile)
        self.connection = psycopg.connect(**options, connect_timeout=10, row_factory=record_factory)

    def execute(self, sql, params=()):
        if isinstance(params, dict):
            sql = re.sub(r':([A-Za-z_][A-Za-z0-9_]*)', r'%(\1)s', sql)
        else:
            sql = sql.replace('?', '%s')
        return self.connection.execute(sql, params)

    def executescript(self, sql):
        for statement in sql.split(';'):
            if statement.strip() and not statement.strip().startswith('PRAGMA'):
                self.execute(statement.replace('id INTEGER PRIMARY KEY', 'id BIGSERIAL PRIMARY KEY')
                             .replace(' REAL', ' DOUBLE PRECISION'))

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
