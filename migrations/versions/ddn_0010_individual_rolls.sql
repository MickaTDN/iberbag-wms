CREATE FUNCTION valmet_roll_materials(uid integer,ctx integer,ids jsonb DEFAULT NULL) RETURNS jsonb LANGUAGE sql STABLE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT COALESCE(jsonb_object_agg(lot,q),'{}'::jsonb) FROM (
 SELECT r.lot,count(*)::integer q FROM valmet_rolls r JOIN valmet_roll_states s ON s.id=r.id AND s.context_id=r.context_id
 WHERE r.unit_id=uid AND r.context_id=ctx AND (CASE WHEN ids IS NULL THEN s.present ELSE ids @> to_jsonb(ARRAY[r.id]) END) GROUP BY r.lot) x
$$;
CREATE FUNCTION valmet_roll_ids_valid(uid integer,ctx integer,ids jsonb,wanted_present boolean DEFAULT true) RETURNS boolean LANGUAGE sql STABLE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT CASE WHEN jsonb_typeof(ids)<>'array' THEN false ELSE
 (SELECT count(*)=count(DISTINCT value) FROM jsonb_array_elements_text(ids)) AND
 NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(ids) j WHERE NOT EXISTS(
 SELECT 1 FROM valmet_rolls r JOIN valmet_roll_states s ON s.id=r.id AND s.context_id=r.context_id
 WHERE r.id=j::integer AND r.unit_id=uid AND r.context_id=ctx AND (wanted_present IS NULL OR s.present=wanted_present))) END
$$;
CREATE FUNCTION valmet_roll_bundle(uid integer,ctx integer,ids jsonb) RETURNS boolean LANGUAGE sql STABLE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT NOT EXISTS(SELECT 1 FROM valmet_roll_states s WHERE s.unit_id=uid AND s.context_id=ctx AND s.present AND NOT s.divisible AND ids @> to_jsonb(ARRAY[s.id]))
 OR NOT EXISTS(SELECT 1 FROM valmet_roll_states s WHERE s.unit_id=uid AND s.context_id=ctx AND s.present AND NOT s.divisible AND NOT ids @> to_jsonb(ARRAY[s.id]))
$$;
CREATE FUNCTION valmet_roll_add(uid integer,ctx integer,data jsonb,eventid integer,moveid integer) RETURNS void LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE item jsonb;rid integer;
BEGIN
 IF pg_trigger_depth()<1 OR jsonb_typeof(data)<>'array' OR jsonb_array_length(data)>500 THEN RAISE EXCEPTION 'Invalid individual roll input'; END IF;
 IF jsonb_array_length(data)>0 AND NOT EXISTS(SELECT 1 FROM valmet_units WHERE id=uid AND context_id=ctx AND kind='rolos' AND number IS NOT NULL AND NOT legacy_unnumbered) THEN RAISE EXCEPTION 'Manual warehouse identity required'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(data) LOOP
   IF jsonb_typeof(item->'divisible')<>'boolean' OR btrim(COALESCE(item->>'lot',''))='' OR length(item->>'lot')>120 OR length(item->>'article')>120
     OR NOT EXISTS(SELECT 1 FROM valmet_items WHERE unit_id=uid AND context_id=ctx AND identification_id IS NOT NULL AND lot=item->>'lot'
       AND (item->>'article' IS NULL OR article=item->>'article')) THEN RAISE EXCEPTION 'Roll needs proven lot/article/classification'; END IF;
   INSERT INTO valmet_rolls(context_id,unit_id,article,lot,source_event,source_move) VALUES(ctx,uid,COALESCE(item->>'article',(SELECT min(article) FROM valmet_items WHERE unit_id=uid AND context_id=ctx AND identification_id IS NOT NULL AND lot=item->>'lot' HAVING count(DISTINCT article)=1)),item->>'lot',eventid,moveid) RETURNING id INTO rid;
   INSERT INTO valmet_roll_states(id,context_id,unit_id,present,divisible) VALUES(rid,ctx,uid,true,(item->>'divisible')::boolean);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION valmet_roll_add(integer,integer,jsonb,integer,integer) FROM PUBLIC;
CREATE FUNCTION valmet_roll_event_apply() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE u record;who record;data jsonb;item jsonb;pos record;fresh boolean;
BEGIN
 IF TG_RELID<>'valmet_roll_events'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' THEN RAISE EXCEPTION 'Invalid roll event trigger'; END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT *,xmin=pg_current_xact_id()::xid AS fresh INTO u FROM valmet_units WHERE id=NEW.unit_id AND context_id=NEW.context_id;
 SELECT * INTO who FROM users WHERE username=NEW.actor AND active;
 IF u.id IS NULL OR u.kind<>'rolos' OR who.id IS NULL OR (NOT u.fresh AND who.role<>'admin') OR (u.fresh AND who.role NOT IN ('admin','operador')) OR btrim(NEW.reason)='' OR btrim(NEW.evidence)='' THEN RAISE EXCEPTION 'Roll authority/proof required'; END IF;
 IF EXISTS(SELECT 1 FROM valmet_active_allocations() a WHERE a.ctx=u.context_id AND a.unit=u.id)
   OR EXISTS(SELECT 1 FROM valmet_inventories i JOIN valmet_positions z ON z.unit_id=u.id AND z.context_id=i.context_id WHERE i.context_id=u.context_id AND i.status<>'closed'
      AND ((i.reference::jsonb->'positions') ? z.id::text OR EXISTS(SELECT 1 FROM valmet_counts c WHERE c.inventory_id=i.id AND c.context_id=i.context_id AND c.target='P:'||z.id)))
   OR valmet_missing(u.id,u.context_id) THEN RAISE EXCEPTION 'Reserved/inventoried/blocked roll unit'; END IF;
 data=NEW.payload::jsonb;
 IF NEW.action='register' THEN
   IF EXISTS(SELECT 1 FROM valmet_rolls WHERE unit_id=u.id AND context_id=u.context_id) THEN RAISE EXCEPTION 'Roll identities already registered'; END IF;
   SELECT * INTO pos FROM valmet_positions WHERE unit_id=u.id AND context_id=u.context_id AND physical_quantity=1;
   IF pos.id IS NULL OR pos.uncertain OR NOT EXISTS(SELECT 1 FROM valmet_identifications WHERE unit_id=u.id AND context_id=u.context_id) THEN RAISE EXCEPTION 'Identify/reconcile physical stock first'; END IF;
   PERFORM valmet_roll_add(u.id,u.context_id,data->'rolls',NEW.id,NULL);
 ELSIF NEW.action='classify' THEN
   IF who.role<>'admin' OR EXISTS(SELECT 1 FROM valmet_shipment_lines l JOIN valmet_positions z ON z.id=l.position_id AND z.context_id=l.context_id WHERE z.unit_id=u.id AND z.context_id=u.context_id)
     OR NOT valmet_roll_ids_valid(u.id,u.context_id,data->'ids') THEN RAISE EXCEPTION 'Classification cannot rewrite shipped/reserved identity'; END IF;
   FOR item IN SELECT value FROM jsonb_array_elements(data->'flags') LOOP
     IF jsonb_typeof(item->'divisible')<>'boolean' OR NOT (data->'ids') @> jsonb_build_array((item->>'id')::integer) THEN RAISE EXCEPTION 'Invalid proven flags'; END IF;
     UPDATE valmet_roll_states SET divisible=(item->>'divisible')::boolean WHERE id=(item->>'id')::integer AND context_id=u.context_id AND unit_id=u.id;
   END LOOP;
 ELSE RAISE EXCEPTION 'Invalid roll event'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_roll_event_projection AFTER INSERT ON valmet_roll_events FOR EACH ROW EXECUTE FUNCTION valmet_roll_event_apply();
CREATE TRIGGER valmet_roll_identity_private BEFORE INSERT OR UPDATE OR DELETE ON valmet_rolls FOR EACH ROW EXECUTE FUNCTION valmet_position_protect();
CREATE TRIGGER valmet_roll_state_private BEFORE INSERT OR UPDATE OR DELETE ON valmet_roll_states FOR EACH ROW EXECUTE FUNCTION valmet_position_protect();
CREATE TRIGGER valmet_roll_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_roll_events FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_roll_identity_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_rolls FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_roll_state_truncate BEFORE TRUNCATE ON valmet_roll_states FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();

CREATE FUNCTION valmet_roll_reservations() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE row record;uid integer;ids jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 FOR row IN SELECT x FROM valmet_preparations prep JOIN valmet_plan_revisions rev ON rev.id=prep.active_revision AND rev.context_id=prep.context_id,
   LATERAL jsonb_array_elements(rev.snapshot::jsonb->'selections') x WHERE prep.context_id=NEW.context_id AND prep.status IN ('reserved','ready') AND x->'unit'->>'kind'='rolos' LOOP
   uid=(row.x->'unit'->>'origin_id')::integer;ids=row.x->'roll_ids';
   IF ids IS NULL OR jsonb_array_length(ids)=0 OR NOT valmet_roll_ids_valid(uid,NEW.context_id,ids) OR NOT valmet_roll_bundle(uid,NEW.context_id,ids)
      OR valmet_roll_materials(uid,NEW.context_id,ids) IS DISTINCT FROM row.x->'materials' THEN RAISE EXCEPTION 'Roll identity/bundle reservation invalid or legacy classification unknown'; END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM valmet_preparations prep JOIN valmet_plan_revisions rev ON rev.id=prep.active_revision AND rev.context_id=prep.context_id,
   LATERAL jsonb_array_elements(rev.snapshot::jsonb->'selections') x,LATERAL jsonb_array_elements_text(x->'roll_ids') rid
   WHERE prep.context_id=NEW.context_id AND prep.status IN ('reserved','ready') AND x->'unit'->>'kind'='rolos' GROUP BY rid HAVING count(*)>1)
   THEN RAISE EXCEPTION 'Roll already reserved'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER valmet_roll_reservation_final AFTER INSERT OR UPDATE ON valmet_preparations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_roll_reservations();

CREATE FUNCTION valmet_roll_line_apply() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE uid integer;data jsonb;reserved jsonb;ids jsonb;
BEGIN
 IF TG_RELID<>'valmet_shipment_lines'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' THEN RAISE EXCEPTION 'Invalid roll shipment trigger'; END IF;
 SELECT z.unit_id INTO uid FROM valmet_positions z JOIN valmet_units u ON u.id=z.unit_id AND u.context_id=z.context_id WHERE z.id=NEW.position_id AND z.context_id=NEW.context_id AND u.kind='rolos';
 IF uid IS NULL THEN RETURN NEW; END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT x INTO data FROM valmet_shipments h,LATERAL jsonb_array_elements(h.snapshot::jsonb->'selections') x WHERE h.id=NEW.shipment_id AND h.context_id=NEW.context_id AND (x->'unit'->>'position_id')::integer=NEW.position_id;
 SELECT x->'roll_ids' INTO reserved FROM valmet_reservations r JOIN valmet_plan_revisions rev ON rev.id=r.revision_id AND rev.context_id=r.context_id,
 LATERAL jsonb_array_elements(rev.snapshot::jsonb->'selections') x WHERE r.id=NEW.reservation_id AND r.context_id=NEW.context_id AND (x->'unit'->>'position_id')::integer=NEW.position_id;
 ids=data->'roll_ids';
 IF ids IS NULL OR jsonb_array_length(ids)=0 OR NOT valmet_roll_ids_valid(uid,NEW.context_id,ids) OR NOT valmet_roll_bundle(uid,NEW.context_id,ids)
   OR NOT reserved @> ids OR valmet_roll_materials(uid,NEW.context_id,ids) IS DISTINCT FROM NEW.materials::jsonb THEN RAISE EXCEPTION 'Invalid individual roll shipment'; END IF;
 UPDATE valmet_roll_states SET present=false WHERE context_id=NEW.context_id AND unit_id=uid AND ids @> to_jsonb(ARRAY[id]);
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_roll_line AFTER INSERT ON valmet_shipment_lines FOR EACH ROW EXECUTE FUNCTION valmet_roll_line_apply();

CREATE FUNCTION valmet_roll_control_apply() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE d record;u record;data jsonb;keep jsonb;restore jsonb;oldids jsonb;rid integer;
BEGIN
 IF TG_RELID<>'valmet_control_moves'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' THEN RAISE EXCEPTION 'Invalid roll control trigger'; END IF;
 SELECT draft.*,z.unit_id INTO d FROM valmet_control_drafts draft JOIN valmet_positions z ON z.id=draft.position_id AND z.context_id=draft.context_id WHERE draft.id=NEW.draft_id AND draft.context_id=NEW.context_id;
 SELECT * INTO u FROM valmet_units WHERE id=d.unit_id AND context_id=NEW.context_id;
 IF u.kind<>'rolos' OR d.kind='transfer' THEN RETURN NEW; END IF;
 data=d.payload::jsonb;keep=data->'roll_change'->'keep';restore=COALESCE(data->'roll_change'->'restore','[]'::jsonb);
 IF keep IS NULL OR NOT valmet_roll_ids_valid(u.id,NEW.context_id,keep) OR NOT valmet_roll_ids_valid(u.id,NEW.context_id,restore,false)
   OR EXISTS(SELECT 1 FROM valmet_active_allocations() a WHERE a.ctx=NEW.context_id AND a.unit=u.id) THEN RAISE EXCEPTION 'Explicit proven roll identities required for adjustment'; END IF;
 FOR rid IN SELECT value::integer FROM jsonb_array_elements_text(restore) LOOP
   IF NOT EXISTS(SELECT 1 FROM valmet_shipment_lines l JOIN valmet_shipments h ON h.id=l.shipment_id AND h.context_id=l.context_id,
     LATERAL jsonb_array_elements(h.snapshot::jsonb->'selections') x,LATERAL jsonb_array_elements(COALESCE(data->'returns','[]'::jsonb)) claim
     WHERE l.context_id=NEW.context_id AND l.id=(claim->>'line')::integer AND (x->'unit'->>'position_id')::integer=l.position_id AND x->'roll_ids' @> to_jsonb(ARRAY[rid])
       AND l.id=(SELECT max(lastline.id) FROM valmet_shipment_lines lastline JOIN valmet_shipments lasth ON lasth.id=lastline.shipment_id AND lasth.context_id=lastline.context_id,
         LATERAL jsonb_array_elements(lasth.snapshot::jsonb->'selections') lastsel WHERE lastline.context_id=NEW.context_id AND lastsel->'roll_ids' @> to_jsonb(ARRAY[rid]) AND (lastsel->'unit'->>'position_id')::integer=lastline.position_id))
       THEN RAISE EXCEPTION 'Restore requires original latest shipment proof for this roll'; END IF;
 END LOOP;
 UPDATE valmet_roll_states SET present=false WHERE unit_id=u.id AND context_id=NEW.context_id AND present AND NOT keep @> to_jsonb(ARRAY[id]);
 UPDATE valmet_roll_states SET present=true WHERE unit_id=u.id AND context_id=NEW.context_id AND restore @> to_jsonb(ARRAY[id]);
 PERFORM valmet_roll_add(u.id,NEW.context_id,COALESCE(data->'roll_change'->'add','[]'::jsonb),NULL,NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_roll_control AFTER INSERT ON valmet_control_moves FOR EACH ROW EXECUTE FUNCTION valmet_roll_control_apply();

CREATE FUNCTION valmet_roll_totals() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE uid integer;total integer;phy integer;mat jsonb;row record;
BEGIN
 IF TG_TABLE_NAME='valmet_shipment_lines' THEN SELECT unit_id INTO uid FROM valmet_positions WHERE id=NEW.position_id AND context_id=NEW.context_id;
 ELSE uid=NEW.unit_id; END IF;
 IF EXISTS(SELECT 1 FROM valmet_rolls WHERE unit_id=uid AND context_id=NEW.context_id) THEN
   SELECT count(*) INTO total FROM valmet_roll_states WHERE unit_id=uid AND context_id=NEW.context_id AND present;
   SELECT COALESCE(sum(physical_quantity),0) INTO phy FROM valmet_positions WHERE unit_id=uid AND context_id=NEW.context_id;
   SELECT COALESCE(jsonb_object_agg(lot,q),'{}'::jsonb) INTO mat FROM (
     SELECT k AS lot,sum(v::integer)::integer q FROM valmet_positions z,LATERAL jsonb_each_text(z.materials::jsonb) kv(k,v) WHERE z.unit_id=uid AND z.context_id=NEW.context_id GROUP BY k) x;
   IF phy<>(CASE WHEN total>0 THEN 1 ELSE 0 END) OR mat IS DISTINCT FROM valmet_roll_materials(uid,NEW.context_id) THEN RAISE EXCEPTION 'Individual rolls differ from stock projection'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER valmet_roll_totals_event AFTER INSERT ON valmet_roll_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_roll_totals();
CREATE CONSTRAINT TRIGGER valmet_roll_totals_line AFTER INSERT ON valmet_shipment_lines DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_roll_totals();
CREATE CONSTRAINT TRIGGER valmet_roll_totals_move AFTER INSERT ON valmet_control_moves DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_roll_totals();
