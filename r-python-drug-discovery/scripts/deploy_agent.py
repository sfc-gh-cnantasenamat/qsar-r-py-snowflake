"""Create the optional Cortex Agent from its versioned YAML specification."""

import argparse
from pathlib import Path

import snowflake.connector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True, help="Snowflake CLI connection name")
    args = parser.parse_args()

    specification = (Path(__file__).resolve().parents[1] / "agent" / "aromatase_agent.yaml").read_text()
    # Reject a malformed closing delimiter rather than interpolating SQL from YAML.
    if "$$" in specification:
        raise ValueError("Agent specification cannot contain a SQL dollar delimiter")

    with snowflake.connector.connect(connection_name=args.connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE OR REPLACE AGENT QSAR_DB.PUBLIC.AROMATASE_AGENT "
                f"FROM SPECIFICATION $$\n{specification}\n$$"
            )
            print(cursor.fetchone()[0])
            cursor.execute(
                "GRANT USAGE ON AGENT QSAR_DB.PUBLIC.AROMATASE_AGENT TO ROLE QSAR_ROLE"
            )
            print(cursor.fetchone()[0])
            cursor.execute(
                "ALTER SNOWFLAKE INTELLIGENCE SNOWFLAKE_INTELLIGENCE_OBJECT_DEFAULT "
                "ADD AGENT QSAR_DB.PUBLIC.AROMATASE_AGENT"
            )
            print(cursor.fetchone()[0])


if __name__ == "__main__":
    main()
