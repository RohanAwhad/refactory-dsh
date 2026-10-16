"""A stand-in for `gh`, the GitHub CLI, over bare repositories on disk.

Only what delivery asks for (#350): who is logged in, a repository's details, creating
one, and listing, opening and reading pull requests. Its state lives under
``FAKE_GH_ROOT``: ``<owner>/<name>.git`` is the repository git pushes to (the tests
point `https://github.com/` there with `url.<base>.insteadOf`), ``<owner>/<name>.json``
what GitHub says about it, and ``<owner>/<name>.pulls.json`` its pull requests. A
``login`` file holds the account gh is logged in to; without one, gh is logged out.
Every call's HOME is appended to ``homes.log``, so a test can see where it ran from.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ["FAKE_GH_ROOT"])


def _fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _option(args: list[str], name: str, default: str = "") -> str:
    return args[args.index(name) + 1] if name in args else default


def _meta(slug: str) -> dict:
    return json.loads((ROOT / f"{slug}.json").read_text())


def _pulls(slug: str) -> list[dict]:
    path = ROOT / f"{slug}.pulls.json"
    return json.loads(path.read_text()) if path.exists() else []


def _save(slug: str, pulls: list[dict]) -> None:
    (ROOT / f"{slug}.pulls.json").write_text(json.dumps(pulls, indent=2))


def _head(slug: str, branch: str) -> str:
    override = ROOT / "head-override"
    if override.exists():
        return override.read_text().strip()
    found = subprocess.run(["git", "--git-dir", str(ROOT / f"{slug}.git"), "rev-parse", "-q", "--verify",
                            f"refs/heads/{branch}"], capture_output=True, text=True)
    return found.stdout.strip()


def _view(slug: str, pull: dict, fields: str) -> dict:
    # `stale-view` holds a head that only the read-back after a push sees, as when GitHub
    # shows a pull request at some other commit than the one just pushed.
    stale = ROOT / "stale-view"
    if fields == "headRefOid,url" and stale.exists():
        return {"headRefOid": stale.read_text().strip(),
                "url": f"https://github.com/{slug}/pull/{pull['number']}"}
    shown = {"url": f"https://github.com/{slug}/pull/{pull['number']}", "number": pull["number"],
             "state": pull["state"], "headRefName": pull["head"], "headRefOid": _head(slug, pull["head"]),
             "baseRefName": pull["base"], "isCrossRepository": pull.get("cross", False),
             "title": pull["title"], "body": pull["body"], "reviews": pull.get("reviews", []),
             "comments": pull.get("comments", [])}
    return {field: shown[field] for field in fields.split(",")}


def _find(ref: str, args: list[str]) -> tuple[str, dict]:
    if ref.startswith("https://github.com/"):
        owner, name, _pull, number = ref.removeprefix("https://github.com/").split("/")[:4]
        slug = f"{owner}/{name}"
    else:
        slug, number = _option(args, "--repo"), ref
    pull = next((pull for pull in _pulls(slug) if str(pull["number"]) == str(number)), None)
    if pull is None:
        _fail(f"GraphQL: Could not resolve to a PullRequest with the number of {number}.")
    return slug, pull


def main(args: list[str]) -> None:
    with (ROOT / "homes.log").open("a") as log:
        log.write(os.environ.get("HOME", "") + "\n")
    if (ROOT / "offline").exists():
        _fail("error connecting to api.github.com\ncheck your internet connection or https://githubstatus.com")
    login = ROOT / "login"
    if not login.exists():
        # gh 2.92 exits 4 here; 2.99 asks GitHub anyway and is refused with a 401.
        if (ROOT / "logged-out-as-401").exists():
            _fail("gh: Requires authentication (HTTP 401)")
        _fail("To get started with GitHub CLI, please run:  gh auth login", 4)
    if args[:2] == ["api", "user"]:
        print(json.dumps({"login": login.read_text().strip()}))
    elif args[0] == "api" and args[1].count("/") == 2:
        slug = args[1].removeprefix("repos/")
        if not (ROOT / f"{slug}.git").is_dir():
            print(json.dumps({"message": "Not Found", "status": "404"}))
            _fail("gh: Not Found (HTTP 404)")
        meta = _meta(slug)
        print(json.dumps({"full_name": slug, "default_branch": meta.get("default_branch", "main"),
                          "created_at": meta.get("created_at"),
                          "archived": meta.get("archived", False), "visibility": meta.get("visibility"),
                          "permissions": {"push": meta.get("push", True)}}))
    elif args[0] == "api" and args[1].endswith("/comments"):
        _repos, owner, name, _pulls_, number, _comments = args[1].split("/")
        _slug, pull = _find(number, ["--repo", f"{owner}/{name}"])
        print(json.dumps([pull.get("review_comments", [])]))
    elif args[:2] == ["repo", "create"]:
        slug = args[2]
        if (ROOT / f"{slug}.git").exists():
            _fail("GraphQL: Name already exists on this account (createRepository)")
        visibility = next(flag.removeprefix("--") for flag in args if flag in ("--private", "--public", "--internal"))
        (ROOT / slug).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(ROOT / f"{slug}.git")], check=True)
        made = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        (ROOT / f"{slug}.json").write_text(json.dumps({"visibility": visibility, "default_branch": "main",
                                                       "created_at": made}))
        print(f"https://github.com/{slug}")
    elif args[:2] == ["pr", "list"]:
        slug, head = _option(args, "--repo"), _option(args, "--head")
        print(json.dumps([_view(slug, pull, _option(args, "--json")) for pull in _pulls(slug)
                          if pull["head"] == head]))
    elif args[:2] == ["pr", "create"]:
        slug, head = _option(args, "--repo"), _option(args, "--head")
        if not _head(slug, head):
            _fail(f"pull request create failed: GraphQL: Head sha can't be blank, No commits between main and {head}")
        pulls = _pulls(slug)
        pulls.append({"number": len(pulls) + 1, "head": head, "base": _option(args, "--base"), "state": "OPEN",
                      "title": _option(args, "--title"), "body": Path(_option(args, "--body-file")).read_text()})
        _save(slug, pulls)
        print(f"https://github.com/{slug}/pull/{len(pulls)}")
    elif args[:2] == ["pr", "view"]:
        slug, pull = _find(args[2], args)
        print(json.dumps(_view(slug, pull, _option(args, "--json"))))
    else:
        _fail(f"fake gh does not know `gh {' '.join(args)}`")


if __name__ == "__main__":
    main(sys.argv[1:])
