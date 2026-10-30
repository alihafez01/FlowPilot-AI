from pydantic import BaseModel, ConfigDict, Field, field_validator


class ICPConfig(BaseModel):
    """
    Ideal Customer Profile (ICP) for a discovery run.

    Limits are deliberately small to control free API usage.
    """

    model_config = ConfigDict(extra="forbid")

    target_industries: list[str] = Field(
        min_length=1,
        max_length=5,
        description="Target industries or company categories",
    )

    target_geographies: list[str] = Field(
        default_factory=lambda: ["United States"],
        min_length=1,
        max_length=5,
    )

    company_types: list[str] = Field(
        default_factory=lambda: ["agency"],
        max_length=5,
    )

    service_keywords: list[str] = Field(
        min_length=1,
        max_length=8,
        description="Services relevant to your offering",
    )

    additional_keywords: list[str] = Field(
        default_factory=list,
        max_length=8,
    )

    include_domains: list[str] = Field(
        default_factory=list,
        max_length=10,
    )

    exclude_domains: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

    max_queries: int = Field(default=3, ge=1, le=3)
    max_results_per_query: int = Field(default=5, ge=1, le=10)
    extract_top_n: int = Field(default=5, ge=0, le=5)

    @field_validator(
        "target_industries",
        "target_geographies",
        "company_types",
        "service_keywords",
        "additional_keywords",
        mode="before",
    )
    @classmethod
    def clean_terms(cls, value):
        if not isinstance(value, list):
            raise ValueError("Provide a list of text values.")

        cleaned = []
        seen = set()

        for item in value:
            if not isinstance(item, str):
                raise ValueError("Every list item must be text.")

            item = item.strip()

            if not item:
                continue

            if len(item) > 100:
                raise ValueError("Each term must be at most 100 characters.")

            normalized = item.casefold()

            if normalized not in seen:
                cleaned.append(item)
                seen.add(normalized)

        return cleaned

    @field_validator("include_domains", "exclude_domains", mode="before")
    @classmethod
    def clean_domain_lists(cls, value):
        if not isinstance(value, list):
            raise ValueError("Provide a list of domains.")

        cleaned = []
        seen = set()

        for item in value:
            if not isinstance(item, str):
                raise ValueError("Every domain must be text.")

            item = item.strip().lower()

            if not item:
                continue

            if "://" in item:
                item = item.split("://", 1)[1]

            item = item.split("/", 1)[0].strip().strip(".")

            if not item or " " in item:
                raise ValueError(f"Invalid domain filter: {item!r}")

            if item.startswith("www."):
                item = item[4:]

            if item not in seen:
                cleaned.append(item)
                seen.add(item)

        return cleaned