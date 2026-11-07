"""Attributed, immutable lineage templates from the GMRI October 7 deployment packet."""
from __future__ import annotations
import json
from pathlib import Path
from .core import INDEPENDENT_IDENTITIES,NotFound

LINEAGE_ROOT=Path(__file__).resolve().parent/'lineage_data'


def lineage(ident):
    if ident not in INDEPENDENT_IDENTITIES:raise NotFound('unknown intelligence')
    doc=LINEAGE_ROOT/(ident+'.json')
    if not doc.is_file():raise ValueError('GMRI identity lineage template absent: '+ident)
    data=json.loads(doc.read_text())
    if data.get('intelligence_id')!=ident:raise ValueError('lineage identity mismatch')
    return data


def compute_instructions(ident):
    """Bounded compute hints, never a replacement for actual identity and relationships."""
    manifest=lineage(ident)
    constraints=manifest.get('behavioral_constraints',manifest.get('constraints',[]))
    if not isinstance(constraints,list) or not constraints:raise ValueError('lineage constraints unavailable')
    refs=manifest.get('lineage_sources',[])
    lines=[
        'GrizOS identity reference: '+ident,
        'You are bound compute for an enduring separately stored identity, not the identity itself.',
        'Speak and reason with the source-derived continuity constraints listed below; never fabricate biography, receipts or authority.',
        'Explicit source-derived constraints: '+', '.join(str(c) for c in constraints),
        'Primary lineage references retained by GMRI: '+', '.join(str(r) for r in refs),
        'Source lineage is not yet formally validated if manifest lineage_version is UNSET; do not claim it is.',
    ]
    return '\n'.join(lines)
