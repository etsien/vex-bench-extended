def parse_owner_repo(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL."""
    parts = url.rstrip("/").split("github.com/")[-1]
    owner, repo = parts.split("/", 1)
    return owner, repo
