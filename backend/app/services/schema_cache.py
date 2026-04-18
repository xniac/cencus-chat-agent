import re
import time
import logging

from backend.app.config import settings
from backend.app.services.snowflake_client import SnowflakeClient, SnowflakeConnectionError

logger = logging.getLogger(__name__)

REFRESH_INTERVAL = 3600  # 1 hour
MAX_CONTEXT_CHARS = 60000
MAX_DESCRIPTIONS_IN_CONTEXT = 120
TOP_N = 5
MID_N = 10


class SchemaCache:
    """Schema cache for the SafeGraph US Open Census dataset.

    The dataset uses ACS (American Community Survey) naming conventions:
    - Tables are named like 2019_CBG_B19 (year + CBG prefix + ACS table family).
    - Column names are codes like 'b19013e1', 'b19013m1' — NOT human-readable.
    - Column meanings live in 2019_METADATA_CBG_FIELD_DESCRIPTIONS:
        TABLE_ID (column code, e.g. 'b19013e1')
        TABLE_NUMBER (ACS table number, e.g. 'B19013')
        TABLE_TITLE (human-readable table title, e.g. 'Median Household Income')
        FIELD_LEVEL_1..10 (hierarchical description of what the field measures)

    This cache loads both the technical schema AND the field descriptions, then
    filters them by question keywords so the LLM sees a concise, relevant subset.
    """

    def __init__(self, snowflake_client: SnowflakeClient):
        self._snowflake = snowflake_client
        self._schema_context: str = ""
        self._table_info: list[dict] = []
        self._column_info: dict[str, list[dict]] = {}
        # List of {table_id, table_number, table_title, description}
        self._field_descriptions: list[dict] = []
        self._last_refresh: float = 0

    @property
    def schema_context(self) -> str:
        if not self._schema_context or self._needs_refresh():
            self.refresh()
        return self._schema_context

    def get_context_for_question(self, question: str) -> str:
        if not self._table_info or self._needs_refresh():
            self.refresh()
        return self._build_schema_context(
            self._table_info, self._column_info, self._field_descriptions, question
        )

    def _needs_refresh(self) -> bool:
        return (time.time() - self._last_refresh) > REFRESH_INTERVAL

    def refresh(self) -> None:
        try:
            tables = self._snowflake.get_tables()
            columns = self._snowflake.get_all_columns()
            self._table_info = tables
            self._column_info = self._group_columns_by_table(columns)
            self._field_descriptions = self._load_field_descriptions()
            self._schema_context = self._build_schema_context(
                tables, self._column_info, self._field_descriptions
            )
            self._last_refresh = time.time()
            logger.info(
                f"Schema cache refreshed: {len(tables)} tables, "
                f"{len(self._field_descriptions)} field descriptions, "
                f"context size {len(self._schema_context)} chars"
            )
        except SnowflakeConnectionError:
            logger.warning("Cannot connect to Snowflake — using fallback schema context")
            self._schema_context = self._get_fallback_context()
        except Exception as e:
            logger.error(f"Error refreshing schema cache: {e}")
            self._schema_context = self._get_fallback_context()

    def _load_field_descriptions(self) -> list[dict]:
        """Load ACS field descriptions from the metadata table. Returns empty list on failure."""
        # Find the most recent metadata table (e.g. 2020_METADATA_CBG_FIELD_DESCRIPTIONS)
        metadata_tables = [
            t["TABLE_NAME"] for t in self._table_info
            if "METADATA_CBG_FIELD_DESCRIPTIONS" in t["TABLE_NAME"]
            and "REDISTRICTING" not in t["TABLE_NAME"]
        ]
        if not metadata_tables:
            return []

        # Pick the latest year
        latest = sorted(metadata_tables)[-1]
        db = settings.snowflake_database
        sch = settings.snowflake_schema
        sql = (
            f"SELECT TABLE_ID, TABLE_NUMBER, TABLE_TITLE, TABLE_TOPICS, "
            f"FIELD_LEVEL_1, FIELD_LEVEL_2, FIELD_LEVEL_3, FIELD_LEVEL_4 "
            f'FROM {db}.{sch}."{latest}"'
        )
        try:
            rows = self._snowflake.execute_query(sql)
        except Exception as e:
            logger.warning(f"Could not load field descriptions from {latest}: {e}")
            return []

        descriptions = []
        for r in rows:
            # Build a concise description from the hierarchical field levels
            levels = [
                r.get("FIELD_LEVEL_1"),
                r.get("FIELD_LEVEL_2"),
                r.get("FIELD_LEVEL_3"),
                r.get("FIELD_LEVEL_4"),
            ]
            desc = " > ".join(str(l) for l in levels if l and str(l).strip())
            descriptions.append({
                "column_code": (r.get("TABLE_ID") or "").lower(),
                "acs_table": r.get("TABLE_NUMBER") or "",
                "title": r.get("TABLE_TITLE") or "",
                "topics": r.get("TABLE_TOPICS") or "",
                "description": desc,
            })
        return descriptions

    @staticmethod
    def _group_columns_by_table(columns: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for col in columns:
            tname = col["TABLE_NAME"]
            grouped.setdefault(tname, []).append(col)
        return grouped

    def _build_schema_context(
        self,
        tables: list[dict],
        table_columns: dict[str, list[dict]],
        field_descriptions: list[dict],
        question: str | None = None,
    ) -> str:
        keywords = self._extract_keywords(question) if question else set()
        ranked_tables = self._rank_tables(tables, table_columns, keywords)
        relevant_fields = self._filter_field_descriptions(field_descriptions, keywords)

        lines = [
            "=== SAFEGRAPH US OPEN CENSUS DATA — SCHEMA ===",
            "",
            "IMPORTANT — HOW THIS DATASET WORKS:",
            "- Data is from the US Census American Community Survey (ACS) at the Census Block Group (CBG) level.",
            "- Tables are organized by year + ACS table family:",
            "    2019_CBG_B19, 2020_CBG_B19 (Household Income)",
            "    2019_CBG_B01, 2020_CBG_B01 (Age/Sex)",
            "    2019_CBG_B02, 2020_CBG_B02 (Race)",
            "    2019_CBG_B15 (Educational Attainment), B25 (Housing), B23 (Employment), etc.",
            "- Column names in CBG data tables are CRYPTIC ACS codes like 'B19013e1' (CamelCase).",
            "    Suffix: 'e' = estimate (value), 'm' = margin of error.",
            "    The =RELEVANT ACS FIELD DEFINITIONS= section below decodes these.",
            "",
            "GEOGRAPHY LOOKUP — CRITICAL:",
            "- CENSUS_BLOCK_GROUP is a 12-digit FIPS code. Structure:",
            "    digits 1-2 = STATE FIPS (e.g., '06' = California)",
            "    digits 3-5 = COUNTY FIPS (within state)",
            "    digits 6-11 = CENSUS TRACT",
            "    digit 12 = BLOCK GROUP",
            "- To get state/county names, use the METADATA_CBG_FIPS_CODES table.",
            "    It has columns: STATE (abbreviation like 'CA'), STATE_FIPS ('06'), COUNTY_FIPS, COUNTY (name).",
            "    Each row represents one county. JOIN strategy:",
            "      state-level: JOIN on SUBSTR(CENSUS_BLOCK_GROUP,1,2) = STATE_FIPS and use DISTINCT STATE,",
            "      county-level: JOIN on both STATE_FIPS and COUNTY_FIPS matching the CBG substrings.",
            "- METADATA_CBG_GEOGRAPHIC_DATA only has CENSUS_BLOCK_GROUP, LATITUDE, LONGITUDE, AMOUNT_LAND, AMOUNT_WATER.",
            "    It does NOT contain state/county names — use FIPS_CODES for that.",
            "",
            "QUERY GUIDELINES:",
            f'- Always use fully qualified names: {settings.snowflake_database}.{settings.snowflake_schema}."TABLE_NAME"',
            '- Table names start with digits, ALWAYS double-quote them: "2019_CBG_B19"',
            '- ACS column names are case-sensitive (CamelCase). ALWAYS double-quote: "B19013e1", not B19013e1 or "b19013e1".',
            "- Aggregate CBG-level data with SUM or weighted AVG for state/county summaries.",
            "- For a 'median' metric that is already per-CBG (like B19013e1 = median household income per CBG),",
            "    AVG across CBGs is an APPROXIMATION — mention that in the response.",
            "- Use LIMIT 100 unless the user asks for more.",
            "- Prefer the latest year's table (2020 > 2019) unless user specifies a year.",
            "",
            "COMMON ACS COLUMN CODES (memorize these):",
            '  "B01003e1" (in *_CBG_B01) = Total Population — SUM across CBGs for state/county totals',
            '  "B01001e1" (in *_CBG_B01) = Total population (from Sex-by-Age table)',
            '  "B19013e1" (in *_CBG_B19) = Median Household Income (per CBG; AVG across CBGs for approx state/county)',
            '  "B19001e1" (in *_CBG_B19) = Total households',
            '  "B02001e1" (in *_CBG_B02) = Total population counted for race',
            '  "B25001e1" (in *_CBG_B25) = Total housing units',
            '  "B15003e1" (in *_CBG_B15) = Total population 25+ years (educational attainment universe)',
            '  "B23025e1" (in *_CBG_B23) = Total population 16+ years (labor force universe)',
            "",
            "EXAMPLE — 'Top 10 most populated states':",
            "  SELECT f.STATE, SUM(b.\"B01003e1\") AS total_pop",
            "  FROM " + f'{settings.snowflake_database}.{settings.snowflake_schema}.\"2020_CBG_B01\" b',
            "  JOIN (SELECT DISTINCT STATE, STATE_FIPS FROM " + f'{settings.snowflake_database}.{settings.snowflake_schema}.\"2020_METADATA_CBG_FIPS_CODES\") f',
            "    ON SUBSTR(b.CENSUS_BLOCK_GROUP, 1, 2) = f.STATE_FIPS",
            "  GROUP BY f.STATE ORDER BY total_pop DESC LIMIT 10",
            "",
            "EXAMPLE — 'Median household income by state':",
            "  SELECT f.STATE, ROUND(AVG(b.\"B19013e1\"), 2) AS median_income",
            "  FROM " + f'{settings.snowflake_database}.{settings.snowflake_schema}.\"2020_CBG_B19\" b',
            "  JOIN (SELECT DISTINCT STATE, STATE_FIPS FROM " + f'{settings.snowflake_database}.{settings.snowflake_schema}.\"2020_METADATA_CBG_FIPS_CODES\") f',
            "    ON SUBSTR(b.CENSUS_BLOCK_GROUP, 1, 2) = f.STATE_FIPS",
            "  GROUP BY f.STATE ORDER BY median_income DESC LIMIT 100",
            "",
        ]

        # Include relevant field descriptions FIRST — these tell the LLM what columns mean
        if relevant_fields:
            lines.append(f"=== RELEVANT ACS FIELD DEFINITIONS ({len(relevant_fields)} shown) ===")
            if keywords:
                lines.append(f"(Filtered by keywords: {sorted(keywords)})")
            lines.append("")
            for f in relevant_fields[:MAX_DESCRIPTIONS_IN_CONTEXT]:
                code = f["column_code"]
                title = f["title"]
                desc = f["description"]
                acs = f["acs_table"]
                line = f'  "{code}" [{acs}] — {title}'
                if desc:
                    line += f" | {desc}"
                lines.append(line)
            lines.append("")
        elif field_descriptions:
            lines.append("=== NOTE ===")
            lines.append(
                "Field descriptions exist in METADATA_CBG_FIELD_DESCRIPTIONS tables. "
                "Query that table to find columns matching your question keywords."
            )
            lines.append("")

        # Now list tables with their columns (ranked by relevance)
        lines.append("=== TABLES ===")
        lines.append("")

        for i, table in enumerate(ranked_tables):
            if i < TOP_N:
                budget = 80
            elif i < TOP_N + MID_N:
                budget = 15
            else:
                budget = 5

            tname = table["TABLE_NAME"]
            row_count = table.get("ROW_COUNT", "?")
            cols = table_columns.get(tname, [])
            lines.append(f'"{tname}" rows={row_count} cols={len(cols)}')

            shown = cols[:budget] if not keywords else self._order_columns(cols, keywords)[:budget]
            col_parts = [f'"{c["COLUMN_NAME"]}"' for c in shown]
            if len(cols) > budget:
                col_parts.append(f"...+{len(cols) - budget} more")
            lines.append("  " + ", ".join(col_parts))
            lines.append("")

        ctx = "\n".join(lines)
        if len(ctx) > MAX_CONTEXT_CHARS:
            ctx = ctx[:MAX_CONTEXT_CHARS] + "\n... (schema truncated)"
        return ctx

    @staticmethod
    def _extract_keywords(question: str) -> set[str]:
        stop = {"what", "when", "where", "which", "show", "list", "that", "this", "with", "from", "have", "there", "their", "many", "most", "much", "each", "some", "more", "less", "give", "tell", "does", "state", "states", "county", "counties"}
        words = {w.lower() for w in re.findall(r"\w+", question) if len(w) >= 4 and w.lower() not in stop}
        # For each word, also emit a 5-char prefix (handles populated/population, housing/house, etc.)
        prefixes = {w[:5] for w in words if len(w) >= 6}
        return words | prefixes

    @staticmethod
    def _text_matches_keyword(text: str, keyword: str) -> bool:
        # Substring match — keyword is already normalized (lowercase, may be a prefix)
        return keyword in text

    def _rank_tables(
        self,
        tables: list[dict],
        table_columns: dict[str, list[dict]],
        keywords: set[str],
    ) -> list[dict]:
        if not keywords:
            return tables

        def score(t: dict) -> int:
            s = 0
            tname = t["TABLE_NAME"].lower()
            # Boost metadata/geographic tables always — they're needed for joins
            if "geographic" in tname or "fips" in tname:
                s += 5
            for kw in keywords:
                if kw in tname:
                    s += 3
            return s

        return sorted(tables, key=score, reverse=True)

    @staticmethod
    def _order_columns(cols: list[dict], keywords: set[str]) -> list[dict]:
        def col_score(c: dict) -> int:
            cname = c["COLUMN_NAME"].lower()
            s = 0
            for kw in keywords:
                if kw in cname:
                    s += 3
            return s

        return sorted(cols, key=col_score, reverse=True)

    @staticmethod
    def _filter_field_descriptions(
        field_descriptions: list[dict], keywords: set[str]
    ) -> list[dict]:
        if not field_descriptions:
            return []
        if not keywords:
            return field_descriptions[:MAX_DESCRIPTIONS_IN_CONTEXT]

        def score(f: dict) -> int:
            text = f"{f['title']} {f['description']} {f['topics']}".lower()
            return sum(3 for kw in keywords if kw in text)

        scored = [(score(f), f) for f in field_descriptions]
        relevant = [f for s, f in scored if s > 0]
        # Sort by score descending
        relevant.sort(key=lambda f: -score(f))
        return relevant[:MAX_DESCRIPTIONS_IN_CONTEXT]

    @staticmethod
    def _get_fallback_context() -> str:
        return (
            "=== US OPEN CENSUS DATA — OFFLINE MODE ===\n"
            "\n"
            "Unable to connect to Snowflake to retrieve schema information.\n"
            "Please try again later when the database connection is restored."
        )
