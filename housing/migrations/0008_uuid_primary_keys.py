import uuid as _uuid

from django.db import migrations, models


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _uuid_field():
    return models.UUIDField(
        default=_uuid.uuid4, editable=False, primary_key=True, serialize=False
    )


def _convert_postgresql(cursor):
    """
    Convert all relevant PK and FK bigint columns to uuid on PostgreSQL.

    Steps:
      1. Drop every FK constraint that references the 5 tables whose PKs
         we are converting (we query information_schema to get their names).
      2. Drop the PK constraints on those tables (required before type change
         on some PG versions).
      3. ALTER COLUMN id TYPE uuid USING gen_random_uuid() on each table.
      4. Re-add PK constraints.
      5. Change FK columns to uuid (USING gen_random_uuid() is safe here
         because the database is always empty at migration time).
      6. Re-add FK constraints so subsequent migrations can introspect them.
    """

    pk_tables = [
        'housing_customuser',
        'housing_house',
        'housing_application',
        'housing_householdmember',
        'housing_allocationhistory',
    ]

    # ── 1. Drop FK constraints that reference any of the pk_tables ───────────
    cursor.execute("""
        SELECT DISTINCT tc.constraint_name, tc.table_name
        FROM   information_schema.table_constraints  AS tc
        JOIN   information_schema.referential_constraints AS rc
               ON  rc.constraint_name   = tc.constraint_name
               AND rc.constraint_schema = tc.table_schema
        JOIN   information_schema.table_constraints  AS tr
               ON  tr.constraint_name  = rc.unique_constraint_name
               AND tr.table_schema     = rc.unique_constraint_schema
        WHERE  tc.constraint_type = 'FOREIGN KEY'
          AND  tc.table_schema    = 'public'
          AND  tr.table_name IN %s
    """, [tuple(pk_tables)])

    fk_rows = cursor.fetchall()
    for constraint_name, table_name in fk_rows:
        cursor.execute(
            f'ALTER TABLE "{table_name}" '
            f'DROP CONSTRAINT IF EXISTS "{constraint_name}"'
        )

    # ── 2 & 3 & 4. Drop PK, change id type, re-add PK ────────────────────────
    for table in pk_tables:
        # Find and drop the PK constraint (name varies, so look it up)
        cursor.execute("""
            SELECT constraint_name
            FROM   information_schema.table_constraints
            WHERE  table_schema   = 'public'
              AND  table_name     = %s
              AND  constraint_type = 'PRIMARY KEY'
        """, [table])
        pk_row = cursor.fetchone()
        if pk_row:
            cursor.execute(
                f'ALTER TABLE "{table}" DROP CONSTRAINT "{pk_row[0]}"'
            )

        cursor.execute(
            f'ALTER TABLE "{table}" '
            f'ALTER COLUMN "id" TYPE uuid USING gen_random_uuid()'
        )
        cursor.execute(
            f'ALTER TABLE "{table}" ADD PRIMARY KEY ("id")'
        )

    # ── 5. Change FK columns to uuid ─────────────────────────────────────────
    fk_columns = [
        ('housing_house',             'allocated_to_id'),
        ('housing_application',       'applicant_id'),
        ('housing_application',       'reviewed_by_id'),
        ('housing_allocationhistory', 'house_id'),
        ('housing_allocationhistory', 'beneficiary_id'),
        ('housing_allocationhistory', 'allocated_by_id'),
        ('housing_householdmember',   'application_id'),
        # Django M2M through-tables for CustomUser
        ('housing_customuser_groups',           'customuser_id'),
        ('housing_customuser_user_permissions', 'customuser_id'),
        # Django admin log references the user model
        ('django_admin_log',                    'user_id'),
    ]

    # Include authtoken if present
    cursor.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE  table_schema = 'public' AND table_name = 'authtoken_token'
    """)
    if cursor.fetchone():
        fk_columns.append(('authtoken_token', 'user_id'))

    for table, col in fk_columns:
        cursor.execute("""
            SELECT data_type
            FROM   information_schema.columns
            WHERE  table_schema = 'public'
              AND  table_name   = %s
              AND  column_name  = %s
        """, [table, col])
        row = cursor.fetchone()
        if row and row[0] != 'uuid':
            cursor.execute(
                f'ALTER TABLE "{table}" '
                f'ALTER COLUMN "{col}" TYPE uuid USING gen_random_uuid()'
            )

    # ── 6. Re-add FK constraints ──────────────────────────────────────────────
    fk_defs = [
        ('housing_house',             'allocated_to_id', 'housing_customuser', 'id', 'SET NULL'),
        ('housing_application',       'applicant_id',    'housing_customuser', 'id', 'CASCADE'),
        ('housing_application',       'reviewed_by_id',  'housing_customuser', 'id', 'SET NULL'),
        ('housing_allocationhistory', 'house_id',        'housing_house',      'id', 'CASCADE'),
        ('housing_allocationhistory', 'beneficiary_id',  'housing_customuser', 'id', 'CASCADE'),
        ('housing_allocationhistory', 'allocated_by_id', 'housing_customuser', 'id', 'SET NULL'),
        ('housing_householdmember',   'application_id',  'housing_application','id', 'CASCADE'),
    ]
    for ftable, fcol, rtable, rcol, on_delete in fk_defs:
        cursor.execute(f"""
            ALTER TABLE "{ftable}"
            ADD FOREIGN KEY ("{fcol}")
            REFERENCES "{rtable}" ("{rcol}")
            ON DELETE {on_delete}
            DEFERRABLE INITIALLY DEFERRED
        """)


def forward(apps, schema_editor):
    """
    Database-level UUID conversion.

    • PostgreSQL: raw SQL via _convert_postgresql() — AlterField cannot cast
      bigint → uuid without gen_random_uuid(); this function handles
      everything (drop FKs, change PK columns, change FK columns, re-add FKs).

    • SQLite: call schema_editor.alter_field() for each model so Django
      performs its standard table-recreation approach.
    """
    vendor = schema_editor.connection.vendor

    if vendor == 'postgresql':
        with schema_editor.connection.cursor() as cursor:
            _convert_postgresql(cursor)

    else:
        # SQLite (and any other backend): use Django's own schema_editor.
        # The historical model from apps.get_model() has the correct
        # _meta.db_table and field definitions for the state *before* 0008.
        for model_name in [
            'CustomUser', 'House', 'Application',
            'HouseholdMember', 'AllocationHistory',
        ]:
            Model = apps.get_model('housing', model_name)
            old_field = Model._meta.get_field('id')
            new_field = _uuid_field()
            new_field.set_attributes_from_name('id')
            schema_editor.alter_field(Model, old_field, new_field)


class Migration(migrations.Migration):
    """
    Converts all model primary keys from BigAutoField (bigint) to UUIDField.

    Uses SeparateDatabaseAndState so that Django's built-in AlterField
    operations ONLY update the migration state (what Django tracks internally)
    and do NOT attempt to run ALTER TABLE … ALTER COLUMN … USING "id"::uuid
    against the database — that cast fails on PostgreSQL for bigint → uuid.

    All actual database work is handled by the forward() RunPython, which
    uses gen_random_uuid() on PostgreSQL and Django's schema_editor on SQLite.

    IMPORTANT: Requires a clean (empty) database.  Existing integer PK values
    cannot be preserved; re-enter data after applying this migration.
    """

    dependencies = [
        ('housing', '0007_customuser_must_change_password'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # database_operations: what actually runs against the DB.
            # RunPython handles both PostgreSQL (raw SQL) and SQLite
            # (schema_editor.alter_field).  AlterField is intentionally
            # absent here so it never generates USING "id"::uuid on PG.
            database_operations=[
                migrations.RunPython(forward, migrations.RunPython.noop),
            ],

            # state_operations: what Django records in its migration state.
            # These keep Django's internal schema knowledge correct so that
            # future migrations generate the right SQL.
            state_operations=[
                migrations.AlterField(
                    model_name='customuser',
                    name='id',
                    field=_uuid_field(),
                ),
                migrations.AlterField(
                    model_name='house',
                    name='id',
                    field=_uuid_field(),
                ),
                migrations.AlterField(
                    model_name='application',
                    name='id',
                    field=_uuid_field(),
                ),
                migrations.AlterField(
                    model_name='householdmember',
                    name='id',
                    field=_uuid_field(),
                ),
                migrations.AlterField(
                    model_name='allocationhistory',
                    name='id',
                    field=_uuid_field(),
                ),
            ],
        ),
    ]
