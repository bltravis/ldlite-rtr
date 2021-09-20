"""
LDLite is a lightweight reporting tool for Okapi-based services.  It is part of
the Library Data Platform project and provides basic LDP functions without
requiring the platform to be installed.

LDLite functions include extracting data from an Okapi instance, transforming
the data for reporting purposes, and storing the data in an analytic database
for further querying.

To install LDLite or upgrade to the latest version:

    python3 -m pip install --upgrade ldlite

Example:

    # Import and initialize LDLite.
    import ldlite
    ld = ldlite.LDLite()

    # Connect to a database.
    from duckdb import connect
    db = connect(database="ldlite.db")
    ld.config_db(db)

    # Connect to Okapi.
    ld.config_okapi(url="https://folio-snapshot-okapi.dev.folio.org",
                    tenant="diku",
                    user="diku_admin",
                    password="admin")

    # Send a CQL query and store the results in table "g", "g_j", etc.
    ld.query(table="g", path="/groups", query="cql.allRecords=1 sortby id")

    # Print the result tables.
    ld.select(table="g")
    ld.select(table="g_j")
    # etc.

"""

import json
import sys

import pandas
import requests
from tqdm import tqdm

from ._csv import _to_csv
from ._jsonx import _transform_json
from ._select import _select
from ._sqlx import _escape_sql

class LDLite:

    def __init__(self):
        """Creates an instance of LDLite.

        Example:

            import ldlite

            ld = ldlite.LDLite()

        """
        self.page_size = 1000
        self._verbose = False
        self._quiet = False

    def _set_page_size(self, page_size):
        self.page_size = page_size

    def config_db(self, db, dbtype):
        """Configures the analytic database.

        The *db* parameter must be an existing database connection.  Supported
        values for *dbtype* are "duckdb" and "postgresql".

        Example:

            from duckdb import connect

            db = connect(database="ldlite.db")

            ld.config_db(db)

        """
        if dbtype is None or dbtype == "":
            raise ValueError("dbtype not specified")
        if dbtype != "duckdb" and dbtype != "postgresql":
            raise ValueError("unknown dbtype \""+str(dbtype)+"\"")
        self.db = db

    def config_okapi(self, url, tenant, user, password):
        """Configures the connection to Okapi.

        The *url*, *tenant*, *user*, and *password* settings are Okapi-specific
        connection parameters.

        Example:

            ld.config_okapi(url="https://folio-snapshot-okapi.dev.folio.org",
                            tenant="diku",
                            user="diku_admin",
                            password="admin")

        """
        self.okapi_url = url
        self.okapi_tenant = tenant
        self.okapi_user = user
        self.okapi_password = password

    def _login(self):
        if self._verbose:
            print("ldlite: logging in to okapi", file=sys.stderr)
        hdr = { 'X-Okapi-Tenant': self.okapi_tenant,
                'Content-Type': 'application/json' }
        data = { 'username': self.okapi_user,
                'password': self.okapi_password }
        resp = requests.post(self.okapi_url+'/authn/login', headers=hdr, data=json.dumps(data))
        return resp.headers['x-okapi-token']

    def query(self, table, path, query, transform=True):
        """Submits a CQL query to an Okapi module, and transforms and stores the result.

        The *path* parameter is the request path, and *query* is the CQL query.
        The result is stored in *table* within the analytic database.  If
        *transform* is True (the default), JSON data are transformed into one
        or more tables that are created in addition to *table*.  New tables add
        a suffix "_j" to *table* and overwrite any existing tables with the
        same name.  A list of newly created tables is returned by this
        function.

        Example:

            ld.query(table="g", path="/groups", query="cql.allRecords=1 sortby id")

        """
        token = self._login()
        cur = self.db.cursor()
        cur.execute("DROP TABLE IF EXISTS "+table)
        cur = self.db.cursor()
        cur.execute("CREATE TABLE "+table+"(__id integer, jsonb varchar)")
        hdr = { 'X-Okapi-Tenant': self.okapi_tenant,
                'X-Okapi-Token': token }
        # First get total number of records
        resp = requests.get(self.okapi_url+path+'?offset=0&limit=1&query='+query, headers=hdr)
        if resp.status_code != 200:
            resp.raise_for_status()
        try:
            j = resp.json()
        except Exception as e:
            raise RuntimeError(resp.text)
        total_records = j["totalRecords"]
        total = total_records if total_records is not None else 0
        if self._verbose:
            print("ldlite: estimated row count: "+str(total), file=sys.stderr)
        # Read result pages
        if not self._quiet:
            print("ldlite: reading results", file=sys.stderr)
        count = 0
        page = 0
        if not self._quiet:
            pbar = tqdm(total=total, bar_format="{l_bar}{bar}| [{elapsed}<{remaining}, {rate_fmt}{postfix}]")
            pbartotal = 0
        while True:
            offset = page * self.page_size
            limit = self.page_size
            resp = requests.get(self.okapi_url+path+'?offset='+str(offset)+'&limit='+str(limit)+'&query='+query, headers=hdr)
            j = resp.json()
            data = list(j.values())[0]
            lendata = len(data)
            if lendata == 0:
                break
            for d in data:
                cur = self.db.cursor()
                cur.execute("INSERT INTO "+table+" VALUES ("+str(count+1)+", '"+_escape_sql(json.dumps(d, indent=4))+"')")
                count += 1
                if not self._quiet:
                    if pbartotal + 1 > total:
                        pbartotal = total
                        pbar.update(total - pbartotal)
                    else:
                        pbartotal += 1
                        pbar.update(1)
            page += 1
        if not self._quiet:
            pbar.close()
        newtables = [table]
        if transform:
            if not self._quiet:
                print("ldlite: transforming data", file=sys.stderr)
            newtables += _transform_json(self.db, table, count, self._quiet)
        if not self._quiet:
            print("ldlite: created tables: "+", ".join(newtables), file=sys.stderr)
        print()
        return newtables

    def quiet(self, enable):
        """Configures suppression of progress messages.

        If *enable* is True, progress messages are suppressed; if False, they
        are not suppressed.

        Example:

            ld.quiet(True)

        """
        if enable is None:
            raise ValueError("quiet(None) is invalid")
        if enable and self._verbose:
            raise ValueError("\"verbose\" and \"quiet\" modes cannot both be enabled")
        self._quiet = enable

    def select(self, table, limit=None, file=None):
        """Prints rows of a table in the analytic database.

        By default all rows of *table* are printed to standard output.  If
        *limit* is specified, then only up to *limit* rows are printed.  If
        *file* is specified, then the rows are printed to *file*.

        Example:

            ld.select(table="g")

        """
        f = sys.stdout if file is None else file
        if self._verbose:
            print("ldlite: reading from table: "+table, file=sys.stderr)
        _select(self.db, table, limit, f)

    def to_csv(self, filename, table):
        """Export a table in the analytic database to a CSV file.

        All rows of *table* are exported to *filename*.

        Example:

            ld.to_csv(table="g", filename="g.csv")

        """
        _to_csv(self.db, table, filename)

    def _verbose(self, enable):
        """Configures verbose output.

        If *enable* is True, verbose output is enabled; if False, it is
        disabled.

        Example:

            ld.verbose(True)

        """
        if enable is None:
            raise ValueError("verbose(None) is invalid")
        if enable and self._quiet:
            raise ValueError("\"verbose\" and \"quiet\" modes cannot both be enabled")
        self._verbose = enable

if __name__ == '__main__':
    pass

