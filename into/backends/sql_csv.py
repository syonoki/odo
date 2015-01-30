import os
import re
import subprocess

import sqlalchemy

from ..regex import RegexDispatcher
from ..append import append
from .csv import CSV
from ..utils import ext

copy_command = RegexDispatcher('copy_command')
execute_copy = RegexDispatcher('execute_copy')


@copy_command.register('.*sqlite')
def copy_sqlite(dialect, tbl, csv, **kwargs):
    abspath = os.path.abspath(csv.path)
    tblname = tbl.name
    dbpath = str(tbl.bind.url).split('///')[-1]

    statement = """
     (echo '.mode csv'; echo '.import {abspath} {tblname}';) | sqlite3 {dbpath}
    """

    return statement.format(**locals())


@execute_copy.register('sqlite')
def execute_copy_sqlite(dialect, engine, statement):
    ps = subprocess.Popen(statement, shell=True, stdout=subprocess.PIPE)
    return ps.stdout.read()


@copy_command.register('postgresql')
def copy_postgres(dialect, tbl, csv, **kwargs):
    abspath = os.path.abspath(csv.path)
    tblname = tbl.name
    format_str = 'csv'
    delimiter = csv.dialect.get('delimiter', ',')
    na_value = ''
    quotechar = csv.dialect.get('quotechar', '"')
    escapechar = csv.dialect.get('escapechar', '\\')
    header = not not csv.has_header
    encoding = csv.encoding or 'utf-8'

    statement = """
        COPY {tblname} FROM '{abspath}'
            (FORMAT {format_str},
             DELIMITER E'{delimiter}',
             NULL '{na_value}',
             QUOTE '{quotechar}',
             ESCAPE '{escapechar}',
             HEADER {header},
             ENCODING '{encoding}');"""

    return statement.format(**locals())


@copy_command.register('mysql.*')
def copy_mysql(dialect, tbl, csv, **kwargs):
    mysql_local = ''
    abspath = os.path.abspath(csv.path)
    tblname = tbl.name
    delimiter = csv.dialect.get('delimiter', ',')
    quotechar = csv.dialect.get('quotechar', '"')
    escapechar = csv.dialect.get('escapechar', '\\')
    lineterminator = csv.dialect.get('lineterminator', r'\n\r')
    skiprows = 1 if csv.has_header else 0
    encoding = csv.encoding or 'utf-8'

    statement = u"""
        LOAD DATA {mysql_local} INFILE '{abspath}'
        INTO TABLE {tblname}
        CHARACTER SET {encoding}
        FIELDS
            TERMINATED BY '{delimiter}'
            ENCLOSED BY '{quotechar}'
            ESCAPED BY '{escapechar}'
        LINES TERMINATED by '{lineterminator}'
        IGNORE {skiprows} LINES;
    """

    return statement.format(**locals())


try:
    import boto
    from into.backends.aws import S3
    from redshift_sqlalchemy.dialect import CopyCommand
    import sqlalchemy as sa
except ImportError:
    pass
else:
    @copy_command.register('redshift.*')
    def copy_redshift(dialect, tbl, csv, schema_name=None, **kwargs):
        assert isinstance(csv, S3(CSV))
        assert csv.path.startswith('s3://')

        cfg = boto.Config()

        aws_access_key_id = cfg.get('Credentials', 'aws_access_key_id')
        aws_secret_access_key = cfg.get('Credentials', 'aws_secret_access_key')

        options = dict(delimiter=kwargs.get('delimiter',
                                            csv.dialect.get('delimiter', ',')),
                       ignore_header=int(kwargs.get('has_header',
                                                    csv.has_header)),
                       empty_as_null=True,
                       blanks_as_null=False)

        if schema_name is None:
            if tbl.schema is not None:
                schema_name = tbl.schema
            else:
                # 'public' by default, this is a postgres convention
                schema_name = sa.inspect(tbl.bind).default_schema_name
        cmd = CopyCommand(schema_name=schema_name,
                          table_name=tbl.name,
                          data_location=csv.path,
                          access_key=aws_access_key_id,
                          secret_key=aws_secret_access_key,
                          options=options,
                          format='CSV',
                          compression={'gz': 'GZIP'}.get(ext(csv.path), ''))
        return re.sub(r'\s+(;)', r'\1', re.sub(r'\s+', ' ', str(cmd))).strip()


@execute_copy.register('.*', priority=9)
def execute_copy_all(dialect, engine, statement):
    conn = engine.raw_connection()
    cursor = conn.cursor()
    cursor.execute(statement)
    conn.commit()


@append.register(sqlalchemy.Table, CSV)
def append_csv_to_sql_table(tbl, csv, **kwargs):
    statement = copy_command(tbl.bind.dialect.name, tbl, csv, **kwargs)
    execute_copy(tbl.bind.dialect.name, tbl.bind, statement)
    return tbl
