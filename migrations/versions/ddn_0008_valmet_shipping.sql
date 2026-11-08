-- Only this versioned migration loads this SQL. Existing sources remain immutable.
CREATE FUNCTION valmet_original_materials(uid integer,ctx integer) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT CASE WHEN u.kind='vasilhame' THEN jsonb_build_object(u.brand,u.physical_quantity)
 ELSE COALESCE((SELECT jsonb_object_agg(lot,q) FROM (
   SELECT i.lot,CASE WHEN u.kind IN ('caixas','vazias') OR (u.kind='paletes' AND u.physical_quantity=1) THEN u.physical_quantity
     WHEN count(i.quantity)=count(*) THEN sum(i.quantity) END AS q
   FROM valmet_items i WHERE i.unit_id=u.id AND i.context_id=ctx AND i.identification_id IS NOT NULL AND i.lot IS NOT NULL GROUP BY i.lot
 ) c),'{}'::jsonb) END FROM valmet_units u WHERE u.id=uid AND u.context_id=ctx
$$;
INSERT INTO valmet_positions(id,context_id,unit_id,root,local,physical_quantity,materials,uncertain)
 SELECT id,context_id,id,true,local,physical_quantity,valmet_original_materials(id,context_id)::text,false FROM valmet_units ORDER BY id;
SELECT setval(pg_get_serial_sequence('valmet_positions','id'),COALESCE(max(id),1),max(id) IS NOT NULL) FROM valmet_positions;

CREATE FUNCTION valmet_active_allocations() RETURNS TABLE(ctx integer,preparation integer,revision integer,unit integer,slot integer,physical integer,materials jsonb,is_exclusive boolean)
 LANGUAGE sql STABLE AS $$
 SELECT p.context_id,p.id,r.id,COALESCE((x->'unit'->>'origin_id')::integer,(x->'unit'->>'id')::integer),
   COALESCE((x->'unit'->>'position_id')::integer,(SELECT z.id FROM valmet_positions z WHERE z.unit_id=(x->'unit'->>'id')::integer AND z.context_id=p.context_id AND z.root)),
   (x->>'physical')::integer,x->'materials',(x->>'exclusive')::boolean
 FROM valmet_preparations p JOIN valmet_plan_revisions r ON r.id=p.active_revision AND r.context_id=p.context_id,
 LATERAL jsonb_array_elements(r.snapshot::jsonb->'selections') x WHERE p.status IN ('reserved','ready')
$$;
CREATE FUNCTION valmet_missing(uid integer,ctx integer) RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT EXISTS(SELECT 1 FROM valmet_occurrences o WHERE o.unit_id=uid AND o.context_id=ctx AND o.kind='missing'
   AND NOT EXISTS(SELECT 1 FROM valmet_occurrences r WHERE r.resolves_id=o.id AND r.context_id=ctx))
$$;
CREATE FUNCTION valmet_take(old jsonb,delta jsonb,moving boolean DEFAULT false) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE pair record; n bigint; cap bigint;
BEGIN
 IF jsonb_typeof(delta)<>'object' OR delta='{}'::jsonb THEN RAISE EXCEPTION 'Empty material movement'; END IF;
 FOR pair IN SELECT * FROM jsonb_each(delta) LOOP
   IF NOT old ? pair.key THEN RAISE EXCEPTION 'Unknown lot'; END IF;
   IF moving AND pair.value='null'::jsonb AND old->pair.key='null'::jsonb THEN old=old-pair.key; CONTINUE; END IF;
   IF pair.value::text !~ '^[1-9][0-9]{0,9}$' THEN RAISE EXCEPTION 'Invalid material movement'; END IF;
   n=pair.value::text::bigint;
   IF old->pair.key='null'::jsonb THEN CONTINUE; END IF;
   cap=(old->>pair.key)::bigint;
   IF n>cap THEN RAISE EXCEPTION 'Insufficient remaining material'; END IF;
   IF n=cap THEN old=old-pair.key; ELSE old=jsonb_set(old,ARRAY[pair.key],to_jsonb(cap-n)); END IF;
 END LOOP;
 RETURN old;
END $$;
CREATE FUNCTION valmet_position_protect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF pg_trigger_depth()<2 THEN RAISE EXCEPTION 'Projection is writable only by ledger triggers'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_projection_private BEFORE INSERT OR UPDATE OR DELETE ON valmet_positions FOR EACH ROW EXECUTE FUNCTION valmet_position_protect();
CREATE TRIGGER valmet_projection_truncate BEFORE TRUNCATE ON valmet_positions FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE FUNCTION valmet_position_init() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF TG_RELID<>'valmet_units'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' THEN RAISE EXCEPTION 'Invalid projection trigger source'; END IF;
 INSERT INTO valmet_positions(context_id,unit_id,root,local,physical_quantity,materials,uncertain)
   VALUES(NEW.context_id,NEW.id,true,NEW.local,NEW.physical_quantity,COALESCE(valmet_original_materials(NEW.id,NEW.context_id),'{}'::jsonb)::text,false);
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_position_initial AFTER INSERT ON valmet_units FOR EACH ROW EXECUTE FUNCTION valmet_position_init();
CREATE FUNCTION valmet_content_projection() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF TG_RELID<>'valmet_items'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' THEN RAISE EXCEPTION 'Invalid projection trigger source'; END IF;
 IF NEW.identification_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM valmet_identifications WHERE id=NEW.identification_id AND context_id=NEW.context_id AND xmin=pg_current_xact_id()::xid)
      OR EXISTS(SELECT 1 FROM valmet_relocations m JOIN valmet_positions z ON z.id=m.position_id AND z.context_id=m.context_id WHERE z.unit_id=NEW.unit_id AND z.context_id=NEW.context_id)
      OR EXISTS(SELECT 1 FROM valmet_shipment_lines m JOIN valmet_positions z ON z.id=m.position_id AND z.context_id=m.context_id WHERE z.unit_id=NEW.unit_id AND z.context_id=NEW.context_id)
      THEN RAISE EXCEPTION 'Confirmed contents cannot be appended or reset'; END IF;
   UPDATE valmet_positions SET materials=valmet_original_materials(NEW.unit_id,NEW.context_id)::text WHERE unit_id=NEW.unit_id AND context_id=NEW.context_id AND root;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_position_contents AFTER INSERT ON valmet_items FOR EACH ROW EXECUTE FUNCTION valmet_content_projection();

CREATE OR REPLACE FUNCTION valmet_active_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Preparation history cannot be deleted'; END IF;
 IF TG_OP='UPDATE' AND (OLD.status IN ('cancelled','shipped') OR NEW.context_id<>OLD.context_id OR NEW.created_by<>OLD.created_by OR NEW.request_key<>OLD.request_key OR NEW.version<OLD.version)
   THEN RAISE EXCEPTION 'Invalid preparation history change'; END IF;
 IF NEW.active_revision IS NOT NULL AND NOT EXISTS(SELECT 1 FROM valmet_plan_revisions WHERE id=NEW.active_revision AND context_id=NEW.context_id AND preparation_id=NEW.id AND action='reserve' AND version<=NEW.version)
   THEN RAISE EXCEPTION 'Invalid active revision'; END IF;
 IF (NEW.status IN ('reserved','ready')) IS DISTINCT FROM (NEW.active_revision IS NOT NULL) THEN RAISE EXCEPTION 'Invalid reservation state'; END IF;
 IF NEW.status='ready' AND NOT EXISTS(SELECT 1 FROM valmet_plan_revisions WHERE id=NEW.active_revision AND context_id=NEW.context_id AND version=NEW.version)
   THEN RAISE EXCEPTION 'Pending edit cannot be loaded'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION valmet_hold_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE snap jsonb; expected jsonb; phys integer; kind text;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT snapshot::jsonb INTO snap FROM valmet_plan_revisions r JOIN valmet_preparations p ON p.id=r.preparation_id AND p.context_id=r.context_id
   WHERE r.id=NEW.revision_id AND r.context_id=NEW.context_id AND r.action='reserve' AND r.version=p.version AND p.status NOT IN ('cancelled','shipped');
 SELECT u.kind INTO kind FROM valmet_units u WHERE u.id=NEW.unit_id AND u.context_id=NEW.context_id AND NOT u.legacy_unnumbered;
 IF kind IS NULL OR snap IS NULL OR valmet_missing(NEW.unit_id,NEW.context_id) THEN RAISE EXCEPTION 'Unavailable physical identity'; END IF;
 SELECT jsonb_object_agg(key,q) INTO expected FROM (
   SELECT m.key,sum(m.value::bigint) q FROM jsonb_array_elements(snap->'selections') x,LATERAL jsonb_each_text(x->'materials') m
   WHERE COALESCE((x->'unit'->>'origin_id')::integer,(x->'unit'->>'id')::integer)=NEW.unit_id GROUP BY m.key) a;
 SELECT CASE WHEN kind IN ('rolos','lab') THEN 1 ELSE sum((x->>'physical')::integer) END INTO phys
   FROM jsonb_array_elements(snap->'selections') x WHERE COALESCE((x->'unit'->>'origin_id')::integer,(x->'unit'->>'id')::integer)=NEW.unit_id;
 IF expected IS DISTINCT FROM NEW.materials::jsonb OR phys IS DISTINCT FROM NEW.physical
   OR NEW.exclusive IS DISTINCT FROM EXISTS(SELECT 1 FROM jsonb_array_elements(snap->'selections') x
     WHERE COALESCE((x->'unit'->>'origin_id')::integer,(x->'unit'->>'id')::integer)=NEW.unit_id AND (x->>'exclusive')::boolean)
   THEN RAISE EXCEPTION 'Reservation differs from immutable review'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION valmet_reservation_totals() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE z record; a record; rule text; used bigint; n integer; ex integer; cap jsonb; whole boolean;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 FOR z IN SELECT DISTINCT v.*,u.kind,u.physical_quantity AS original FROM valmet_positions v JOIN valmet_units u ON u.id=v.unit_id AND u.context_id=v.context_id
   JOIN valmet_active_allocations() alloc ON alloc.slot=v.id AND alloc.ctx=v.context_id WHERE alloc.ctx=NEW.context_id
 LOOP
   SELECT sum(physical),count(*),count(*) FILTER(WHERE is_exclusive) INTO used,n,ex FROM valmet_active_allocations() WHERE slot=z.id AND ctx=z.context_id;
   SELECT separation INTO rule FROM valmet_handling WHERE unit_id=z.unit_id AND context_id=z.context_id ORDER BY id DESC LIMIT 1;
   whole=(z.kind IN ('caixas','pecas') OR (z.kind IN ('paletes','vazias') AND z.original=1) OR (z.kind='rolos' AND COALESCE(rule,'unknown')<>'yes'));
   IF (ex>0 AND n>1) OR (z.kind NOT IN ('rolos','lab') AND used>z.physical_quantity) OR (whole AND (ex<>1 OR used<>z.physical_quantity))
     THEN RAISE EXCEPTION 'Physical stock already reserved'; END IF;
   FOR a IN SELECT key,sum(value::bigint) AS q FROM valmet_active_allocations() h,LATERAL jsonb_each_text(h.materials) m WHERE h.slot=z.id AND h.ctx=z.context_id GROUP BY key LOOP
     cap=z.materials::jsonb->a.key;
     IF cap IS NULL OR a.q<=0 THEN RAISE EXCEPTION 'Material already reserved'; END IF;
     IF cap='null'::jsonb THEN
       IF NOT(z.kind='lab' AND ex=1 AND n=1) THEN RAISE EXCEPTION 'Unknown reserved material'; END IF;
     ELSIF a.q>cap::text::bigint OR (whole AND a.q<>cap::text::bigint) THEN RAISE EXCEPTION 'Material already reserved'; END IF;
   END LOOP;
   IF whole AND EXISTS(SELECT 1 FROM jsonb_object_keys(z.materials::jsonb) k WHERE NOT EXISTS(SELECT 1 FROM valmet_active_allocations() h WHERE h.ctx=z.context_id AND h.slot=z.id AND h.materials ? k))
     THEN RAISE EXCEPTION 'Incomplete whole unit'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM valmet_active_allocations() alloc LEFT JOIN valmet_positions posrow ON posrow.id=alloc.slot AND posrow.context_id=alloc.ctx AND posrow.unit_id=alloc.unit WHERE alloc.ctx=NEW.context_id AND posrow.id IS NULL)
   THEN RAISE EXCEPTION 'Wrong-context reservation slot'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION valmet_apply_line() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE z record; u record; h record; header record; sel jsonb; rest jsonb; expected integer; cap bigint; m record; whole boolean; rule text;
BEGIN
 IF TG_RELID<>'valmet_shipment_lines'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'BEFORE' THEN RAISE EXCEPTION 'Invalid projection trigger source'; END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO z FROM valmet_positions WHERE id=NEW.position_id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO u FROM valmet_units WHERE id=z.unit_id AND context_id=NEW.context_id;
 SELECT * INTO header FROM valmet_shipments WHERE id=NEW.shipment_id AND context_id=NEW.context_id;
 SELECT * INTO h FROM valmet_reservations WHERE id=NEW.reservation_id AND context_id=NEW.context_id;
 IF u.id IS NULL OR h.unit_id<>u.id OR h.revision_id<>header.revision_id OR u.legacy_unnumbered OR (u.kind='vazias' AND u.number IS NULL)
   OR z.uncertain OR valmet_missing(u.id,NEW.context_id) THEN RAISE EXCEPTION 'Unavailable shipment unit'; END IF;
 IF NOT EXISTS(SELECT 1 FROM valmet_preparations p WHERE p.id=header.preparation_id AND p.context_id=NEW.context_id AND p.status='ready' AND p.active_revision=header.revision_id)
   THEN RAISE EXCEPTION 'Preparation not ready'; END IF;
 SELECT x INTO sel FROM valmet_plan_revisions r,LATERAL jsonb_array_elements(r.snapshot::jsonb->'selections') x WHERE r.id=h.revision_id AND r.context_id=NEW.context_id
   AND COALESCE((x->'unit'->>'position_id')::integer,(SELECT id FROM valmet_positions WHERE unit_id=h.unit_id AND context_id=NEW.context_id AND root))=z.id;
 IF sel IS NULL THEN RAISE EXCEPTION 'Position not reserved'; END IF;
 FOR m IN SELECT * FROM jsonb_each_text(NEW.materials::jsonb) LOOP
   SELECT COALESCE(sum((l.materials::jsonb->>m.key)::bigint),0) INTO cap FROM valmet_shipment_lines l WHERE l.reservation_id=h.id AND l.context_id=NEW.context_id AND l.position_id=z.id;
   IF NOT (sel->'materials') ? m.key OR m.value !~ '^[1-9][0-9]{0,9}$' OR m.value::bigint+cap>(sel->'materials'->>m.key)::bigint THEN RAISE EXCEPTION 'Over shipment of reservation'; END IF;
 END LOOP;
 rest=valmet_take(z.materials::jsonb,NEW.materials::jsonb);
 SELECT separation INTO rule FROM valmet_handling WHERE unit_id=u.id AND context_id=u.context_id ORDER BY id DESC LIMIT 1;
 whole=(u.kind IN ('caixas','pecas','vazias') OR (u.kind='paletes' AND u.physical_quantity=1) OR (u.kind='rolos' AND COALESCE(rule,'unknown')<>'yes'));
 IF whole THEN
   IF NEW.materials::jsonb<>z.materials::jsonb OR rest<>'{}'::jsonb THEN RAISE EXCEPTION 'Indivisible shipment'; END IF;
   expected=z.physical_quantity;
 ELSIF u.kind='rolos' THEN expected=CASE WHEN rest='{}'::jsonb THEN z.physical_quantity ELSE 0 END;
 ELSIF u.kind='lab' THEN
   IF EXISTS(SELECT 1 FROM jsonb_each(z.materials::jsonb) jm WHERE jm.value='null'::jsonb) AND NOT NEW.unknown_ack THEN RAISE EXCEPTION 'Explicit unknown LAB validation required'; END IF;
   expected=CASE WHEN NEW.physical_quantity>0 AND rest='{}'::jsonb THEN z.physical_quantity ELSE 0 END;
 ELSE SELECT sum(value::integer) INTO expected FROM jsonb_each_text(NEW.materials::jsonb); END IF;
 IF NEW.physical_quantity<>expected OR expected>z.physical_quantity THEN RAISE EXCEPTION 'Invalid physical shipment'; END IF;
 UPDATE valmet_positions SET physical_quantity=physical_quantity-expected,materials=rest::text,
   uncertain=uncertain OR (u.kind='lab' AND EXISTS(SELECT 1 FROM jsonb_each(z.materials::jsonb) jm WHERE jm.value='null'::jsonb)) WHERE id=z.id;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_line_projection BEFORE INSERT ON valmet_shipment_lines FOR EACH ROW EXECUTE FUNCTION valmet_apply_line();

CREATE FUNCTION valmet_apply_location() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE z record; u record; dest integer; rem jsonb; cap integer; used bigint; q integer; m record;
BEGIN
 IF TG_RELID<>'valmet_relocations'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'BEFORE' THEN RAISE EXCEPTION 'Invalid projection trigger source'; END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO z FROM valmet_positions WHERE id=NEW.position_id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO u FROM valmet_units WHERE id=z.unit_id AND context_id=NEW.context_id;
 SELECT capacidade INTO cap FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.target_local AND active;
 IF NOT FOUND OR z.id IS NULL OR z.local IS NOT DISTINCT FROM NEW.target_local OR btrim(NEW.note)='' OR NEW.physical_quantity<=0 OR NEW.physical_quantity>z.physical_quantity
   OR NOT EXISTS(SELECT 1 FROM valmet_plan_revisions r,LATERAL jsonb_array_elements(r.snapshot::jsonb->'selections') x WHERE r.preparation_id=NEW.preparation_id AND r.context_id=NEW.context_id AND r.action='reserve'
     AND COALESCE((x->'unit'->>'origin_id')::integer,(x->'unit'->>'id')::integer)=u.id)
   THEN RAISE EXCEPTION 'Unproven location correction'; END IF;
 IF u.kind='vazias' AND NEW.target_local<>'Chão' THEN RAISE EXCEPTION 'Empty boxes remain on floor'; END IF;
 IF u.kind IN ('rolos','lab','caixas','pecas','vazias') OR (u.kind='paletes' AND u.physical_quantity=1) THEN
   IF NEW.physical_quantity<>z.physical_quantity OR NEW.materials::jsonb<>z.materials::jsonb THEN RAISE EXCEPTION 'Cannot split physical unit'; END IF;
 ELSE SELECT sum(value::integer) INTO q FROM jsonb_each_text(NEW.materials::jsonb);
   IF q<>NEW.physical_quantity THEN RAISE EXCEPTION 'Group quantity mismatch'; END IF;
 END IF;
 rem=CASE WHEN z.materials::jsonb='{}'::jsonb AND NEW.materials::jsonb='{}'::jsonb AND NEW.physical_quantity=z.physical_quantity THEN '{}'::jsonb
   ELSE valmet_take(z.materials::jsonb,NEW.materials::jsonb,true) END;
 SELECT COALESCE(sum(physical_quantity),0)+(SELECT COALESCE(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=NEW.target_local)
   INTO used FROM valmet_positions WHERE context_id=NEW.context_id AND local=NEW.target_local;
 IF cap IS NOT NULL AND used+NEW.physical_quantity>cap THEN RAISE EXCEPTION 'Location capacity exceeded'; END IF;
 UPDATE valmet_positions SET physical_quantity=physical_quantity-NEW.physical_quantity,materials=rem::text WHERE id=z.id;
 SELECT id INTO dest FROM valmet_positions WHERE context_id=NEW.context_id AND unit_id=u.id AND local=NEW.target_local;
 IF dest IS NULL THEN
   INSERT INTO valmet_positions(context_id,unit_id,root,local,physical_quantity,materials,uncertain) VALUES(NEW.context_id,u.id,false,NEW.target_local,0,'{}',false) RETURNING id INTO dest;
 END IF;
 UPDATE valmet_positions SET physical_quantity=physical_quantity+NEW.physical_quantity,uncertain=uncertain OR z.uncertain WHERE id=dest;
 FOR m IN SELECT * FROM jsonb_each(NEW.materials::jsonb) LOOP
   UPDATE valmet_positions SET materials=jsonb_set(materials::jsonb,ARRAY[m.key],CASE WHEN m.value='null'::jsonb THEN 'null'::jsonb ELSE to_jsonb(COALESCE((materials::jsonb->>m.key)::bigint,0)+m.value::text::bigint) END)::text WHERE id=dest;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_location_projection BEFORE INSERT ON valmet_relocations FOR EACH ROW EXECUTE FUNCTION valmet_apply_location();
CREATE CONSTRAINT TRIGGER valmet_location_reservations AFTER INSERT ON valmet_relocations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_reservation_totals();
CREATE CONSTRAINT TRIGGER valmet_line_reservations AFTER INSERT ON valmet_shipment_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_reservation_totals();

CREATE FUNCTION valmet_shipment_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actual jsonb; wanted jsonb;
BEGIN
 SELECT jsonb_agg(jsonb_build_object('slot',position_id,'physical',physical_quantity,'materials',materials::jsonb) ORDER BY position_id) INTO actual FROM valmet_shipment_lines WHERE shipment_id=NEW.id AND context_id=NEW.context_id;
 SELECT jsonb_agg(jsonb_build_object('slot',(x->'unit'->>'position_id')::integer,'physical',(x->>'out_physical')::integer,'materials',x->'materials') ORDER BY (x->'unit'->>'position_id')::integer) INTO wanted FROM jsonb_array_elements(NEW.snapshot::jsonb->'selections') x;
 IF actual IS NULL OR actual IS DISTINCT FROM wanted OR jsonb_array_length(NEW.references::jsonb)<1 OR jsonb_array_length(NEW.references::jsonb)>30 THEN RAISE EXCEPTION 'Incomplete confirmed shipment'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER valmet_complete_shipment AFTER INSERT ON valmet_shipments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_shipment_complete();

CREATE FUNCTION valmet_occurrence_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF NEW.kind NOT IN ('missing','location','substitution','quantity','not_loaded','resolved') THEN RAISE EXCEPTION 'Invalid occurrence'; END IF;
 IF NEW.kind='resolved' THEN
   IF NOT EXISTS(SELECT 1 FROM valmet_occurrences o WHERE o.id=NEW.resolves_id AND o.context_id=NEW.context_id
     AND o.preparation_id=NEW.preparation_id AND o.unit_id=NEW.unit_id AND o.kind<>'resolved') THEN RAISE EXCEPTION 'Mismatched occurrence resolution'; END IF;
 ELSE
   IF NEW.resolves_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM valmet_plan_revisions r,LATERAL jsonb_array_elements(r.snapshot::jsonb->'selections') x
     WHERE r.preparation_id=NEW.preparation_id AND r.context_id=NEW.context_id AND r.action='reserve'
       AND COALESCE((x->'unit'->>'origin_id')::integer,(x->'unit'->>'id')::integer)=NEW.unit_id)
     THEN RAISE EXCEPTION 'Occurrence outside preparation'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_occurrence_source BEFORE INSERT ON valmet_occurrences FOR EACH ROW EXECUTE FUNCTION valmet_occurrence_guard();

CREATE TRIGGER valmet_shipping_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_shipments FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_shipping_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_shipment_lines FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_shipping_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_occurrences FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_shipping_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_relocations FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_shipping_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_documents FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
