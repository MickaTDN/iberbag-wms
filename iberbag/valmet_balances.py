"""Remaining positions are projections, never new physical warehouse identities."""
import json
from types import SimpleNamespace
from sqlalchemy import select
from .database import ValmetPosition,ValmetUnit,ValmetOccurrence

def source_key(position):return position.unit_id if position.root else -position.id

def view(position,unit):
    values={c.name:getattr(unit,c.name) for c in ValmetUnit.__table__.columns}
    values.update(id=source_key(position),origin_id=unit.id,position_id=position.id,
        original_physical=unit.physical_quantity,physical_quantity=position.physical_quantity,local=position.local,
        materials=json.loads(position.materials),uncertain=position.uncertain)
    return SimpleNamespace(**values)

def position_for(db,key):
    return (db.get(ValmetPosition,-key) if key<0 else
        db.scalar(select(ValmetPosition).where(ValmetPosition.unit_id==key,ValmetPosition.root.is_(True))))

def blocked_units(db):
    occurrences=db.scalars(select(ValmetOccurrence)).all()
    resolved={r.resolves_id for r in occurrences if r.resolves_id is not None}
    return {r.unit_id for r in occurrences if r.kind=='missing' and r.id not in resolved}

def allocation_rows(db,exclude=None):
    from .database import ValmetPreparation as Prep,ValmetPlanRevision as Rev
    query=select(Rev).join(Prep,Prep.active_revision==Rev.id).where(Prep.status.in_(('reserved','ready')))
    if exclude is not None:query=query.where(Prep.id!=exclude)
    rows=[]
    for revision in db.scalars(query):
        for s in json.loads(revision.snapshot)['selections']:
            u=s['unit'];origin=u.get('origin_id',u['id'])
            if 'position_id' not in u:
                pos=position_for(db,u['id'])
                u.update(origin_id=origin,position_id=pos.id,id=source_key(pos))
            rows.append(dict(s,preparation_id=revision.preparation_id,revision_id=revision.id))
    return rows
