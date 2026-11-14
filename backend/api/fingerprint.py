"""Empreintes stables des entrées d'un calcul (Lot 3, refonte)."""

import hashlib
import json


def stable_hash(*parts) -> str:
    """sha256 d'une représentation JSON canonique (clés triées, flottants arrondis à 1e-9)."""
    def norm(x):
        if isinstance(x, float):
            return round(x, 9)
        if isinstance(x, dict):
            return {str(k): norm(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [norm(v) for v in x]
        return x
    raw = json.dumps([norm(p) for p in parts], sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()
