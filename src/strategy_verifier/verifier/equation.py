"""The equation stamp: market + technique + order = result.

Every product report ends with a block that hashes its three inputs
and its own body. Identical market data, an identical technique file,
and an identical order reproduce an identical result hash; any
difference in the result hash proves a difference in an input. The
stamp turns determinism from a property into an audit: overfitting
requires changing an input, and changed inputs cannot share a stamp.

Research diagnostics only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError

_CHUNK = 1 << 20


def file_sha256(path: Path) -> str:
    if not isinstance(path, Path):
        raise DomainValidationError("path must be a Path")
    if not path.exists():
        raise DomainValidationError(f"input does not exist: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def render_equation_block(
    market_paths: list[Path],
    technique_path: Path | None,
    order: str,
    result_body: str,
) -> str:
    if not isinstance(order, str) or not order.strip():
        raise DomainValidationError("order must be a non-empty string")
    if not isinstance(result_body, str):
        raise DomainValidationError("result_body must be a string")
    market_lines = []
    market_digests = []
    for path in sorted(market_paths, key=lambda item: item.name):
        digest = file_sha256(path)
        market_digests.append(digest)
        market_lines.append(f"- {path.name}: sha256={digest}")
    combined = hashlib.sha256(
        "".join(market_digests).encode("utf-8")
    ).hexdigest()
    result_hash = hashlib.sha256(
        result_body.encode("utf-8")
    ).hexdigest()
    lines = ["equation:", "market:"]
    lines.extend(market_lines if market_lines else ["- none"])
    lines.append(f"market_combined: sha256={combined}")
    if technique_path is not None:
        lines.append(
            f"technique: {technique_path.name}: "
            f"sha256={file_sha256(technique_path)}"
        )
    lines.append(f"order: {order}")
    lines.append(f"result: sha256={result_hash}")
    lines.append(
        "determinism: the same market, technique, and order reproduce "
        "this result byte for byte; a different result hash proves a "
        "different input"
    )
    lines.append("")
    return "\n".join(lines)
