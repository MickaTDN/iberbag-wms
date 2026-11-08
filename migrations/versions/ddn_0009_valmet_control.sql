CREATE FUNCTION valmet_control_unit_epoch(uid integer,ctx integer,ignored_inventory integer DEFAULT NULL) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object(
 'ident',(SELECT COALESCE(max(id),0) FROM valmet_identifications WHERE context_id=ctx AND unit_id=uid),
 'items',(SELECT COALESCE(max(id),0) FROM valmet_items WHERE context_id=ctx AND unit_id=uid),
 'policy',(SELECT COALESCE(max(id),0) FROM valmet_handling WHERE context_id=ctx AND unit_id=uid),
 'occurrences',(SELECT COALESCE(max(id),0) FROM valmet_occurrences WHERE context_id=ctx AND unit_id=uid),
 'loads',(SELECT COALESCE(max(l.id),0) FROM valmet_shipment_lines l JOIN valmet_positions z ON z.id=l.position_id AND z.context_id=l.context_id WHERE l.context_id=ctx AND z.unit_id=uid),
 'locations',(SELECT COALESCE(max(m.id),0) FROM valmet_relocations m JOIN valmet_positions z ON z.id=m.position_id AND z.context_id=m.context_id WHERE m.context_id=ctx AND z.unit_id=uid),
 'control',(SELECT COALESCE(max(m.id),0) FROM valmet_control_moves m JOIN valmet_control_drafts d ON d.id=m.draft_id AND d.context_id=m.context_id WHERE m.context_id=ctx AND m.unit_id=uid AND (ignored_inventory IS NULL OR d.inventory_id IS DISTINCT FROM ignored_inventory)),
 'reservation',(SELECT COALESCE(max(r.id),0) FROM valmet_plan_revisions r WHERE r.context_id=ctx AND r.preparation_id IN (
   SELECT old.preparation_id FROM valmet_plan_revisions old JOIN valmet_reservations h ON h.revision_id=old.id AND h.context_id=old.context_id WHERE h.context_id=ctx AND h.unit_id=uid)))
$$;
CREATE FUNCTION valmet_control_position_state(pid integer,ctx integer,ignored_inventory integer DEFAULT NULL) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('id',z.id,'unit',z.unit_id,'local',z.local,'physical',z.physical_quantity,'materials',z.materials::jsonb,'uncertain',z.uncertain,
 'epoch',valmet_control_unit_epoch(z.unit_id,ctx,ignored_inventory)) FROM valmet_positions z WHERE z.id=pid AND z.context_id=ctx
$$;
CREATE FUNCTION valmet_control_reference(pid integer,ctx integer,target text,ignored_inventory integer DEFAULT NULL) RETURNS jsonb LANGUAGE sql STABLE AS $$
 SELECT jsonb_build_object('position',valmet_control_position_state(pid,ctx,ignored_inventory),
   'source_catalogue',(SELECT to_jsonb(l) FROM locations l WHERE l.context_id=ctx AND l.codigo=z.local),
   'target_catalogue',(SELECT to_jsonb(l) FROM locations l WHERE l.context_id=ctx AND l.codigo=target))
 FROM valmet_positions z WHERE z.id=pid AND z.context_id=ctx
$$;
CREATE FUNCTION valmet_control_record_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Control history cannot be deleted'; END IF;
 IF TG_OP='UPDATE' AND (OLD.status IN ('confirmed','closed') OR OLD.context_id<>NEW.context_id OR OLD.created_by<>NEW.created_by OR OLD.request_key<>NEW.request_key OR NEW.version<OLD.version)
   THEN RAISE EXCEPTION 'Invalid control record change'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_inventory_guard BEFORE UPDATE OR DELETE ON valmet_inventories FOR EACH ROW EXECUTE FUNCTION valmet_control_record_guard();
CREATE TRIGGER valmet_control_draft_guard BEFORE UPDATE OR DELETE ON valmet_control_drafts FOR EACH ROW EXECUTE FUNCTION valmet_control_record_guard();

CREATE FUNCTION valmet_apply_control() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE d record; z record; u record; who record; inv record; c record; data jsonb; after_mat jsonb; after_phy integer; after_uncertain boolean;
 dest_code text; dest integer; dest_phy integer; current_ref jsonb; pair record; cap integer; used bigint; identifiable boolean; individual boolean;
 positive boolean; source_rem jsonb; source_phy integer; known jsonb; remain jsonb; moved_phy integer; total_other bigint; actual jsonb; claim record; line record; returned bigint; delta bigint; resolved record;
BEGIN
 IF TG_RELID<>'valmet_control_moves'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'BEFORE' THEN RAISE EXCEPTION 'Invalid control trigger source'; END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO d FROM valmet_control_drafts WHERE id=NEW.draft_id AND context_id=NEW.context_id;
 SELECT * INTO z FROM valmet_positions WHERE id=d.position_id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO u FROM valmet_units WHERE id=z.unit_id AND context_id=NEW.context_id;
 SELECT * INTO who FROM users WHERE username=NEW.actor AND active;
 IF d.id IS NULL OR z.id IS NULL OR NEW.unit_id<>z.unit_id OR who.id IS NULL OR (who.role<>'admin' AND who.id<>d.created_by)
   OR (d.kind='adjustment' AND who.role<>'admin') OR d.status<>'draft' THEN RAISE EXCEPTION 'Invalid control authority/state'; END IF;
 data=d.payload::jsonb;dest_code=data->>'target';
 current_ref=valmet_control_reference(z.id,NEW.context_id,dest_code,d.inventory_id);
 IF current_ref IS DISTINCT FROM d.reference::jsonb THEN RAISE EXCEPTION 'Stale stock/reservation reference; recount/review required'; END IF;
 IF btrim(COALESCE(data->>'reason',''))='' OR btrim(COALESCE(data->>'evidence',''))='' THEN RAISE EXCEPTION 'Reason and evidence required'; END IF;
 IF NEW.snapshot::jsonb IS DISTINCT FROM jsonb_build_object('kind',d.kind,'before',d.reference::jsonb,'operation',data) THEN RAISE EXCEPTION 'Control snapshot mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM valmet_active_allocations() a WHERE a.ctx=NEW.context_id AND a.unit=z.unit_id) THEN RAISE EXCEPTION 'Reserved/ready stock cannot be controlled'; END IF;
 IF d.inventory_id IS NOT NULL THEN
   SELECT * INTO inv FROM valmet_inventories WHERE id=d.inventory_id AND context_id=NEW.context_id;
   SELECT * INTO c FROM valmet_counts WHERE id=d.count_id AND inventory_id=d.inventory_id AND context_id=NEW.context_id;
   IF inv.status<>'submitted' OR c.id IS NULL OR c.id<>(SELECT max(id) FROM valmet_counts WHERE inventory_id=c.inventory_id AND target=c.target AND context_id=NEW.context_id)
     THEN RAISE EXCEPTION 'Inventory count is not the latest submitted evidence'; END IF;
 ELSIF d.count_id IS NOT NULL THEN RAISE EXCEPTION 'Count has no inventory'; END IF;
 identifiable=EXISTS(SELECT 1 FROM valmet_identifications WHERE unit_id=u.id AND context_id=u.context_id) AND NOT u.legacy_unnumbered
   AND (u.kind NOT IN ('rolos','lab','caixas','pecas','vazias') OR u.number IS NOT NULL);
 individual=u.kind IN ('rolos','lab','caixas','pecas','vazias') OR (u.kind='paletes' AND u.physical_quantity=1);
 known=valmet_original_materials(u.id,u.context_id);
 IF d.kind='transfer' THEN
   IF NOT identifiable OR z.uncertain OR EXISTS(SELECT 1 FROM valmet_occurrences o WHERE o.context_id=u.context_id AND o.unit_id=u.id AND o.kind<>'resolved'
       AND NOT EXISTS(SELECT 1 FROM valmet_occurrences r WHERE r.resolves_id=o.id AND r.context_id=o.context_id)) THEN RAISE EXCEPTION 'Blocked/unidentified transfer'; END IF;
   IF z.local IS NULL AND (u.kind<>'vasilhame' OR who.role<>'admin' OR NOT COALESCE((data->>'located_proof')::boolean,false)) THEN RAISE EXCEPTION 'Unknown source needs administrative proof'; END IF;
   moved_phy=(data->>'physical')::integer;after_mat=data->'materials';
   IF moved_phy<=0 OR moved_phy>z.physical_quantity OR dest_code IS NOT DISTINCT FROM z.local THEN RAISE EXCEPTION 'Invalid transfer quantity/destination'; END IF;
   IF individual THEN
     IF moved_phy<>z.physical_quantity OR after_mat<>z.materials::jsonb THEN RAISE EXCEPTION 'Physical identity cannot be split'; END IF;
   ELSIF (SELECT COALESCE(sum(value::integer),0) FROM jsonb_each_text(after_mat))<>moved_phy THEN RAISE EXCEPTION 'Group composition mismatch'; END IF;
   remain=CASE WHEN after_mat='{}'::jsonb AND moved_phy=z.physical_quantity THEN '{}'::jsonb ELSE valmet_take(z.materials::jsonb,after_mat,true) END;
   after_phy=moved_phy;after_uncertain=false;
 ELSE
   after_phy=(data->>'physical')::integer;after_mat=data->'materials';source_rem=COALESCE(data->'source_remaining'->'materials','{}'::jsonb);source_phy=COALESCE((data->'source_remaining'->>'physical')::integer,0);after_uncertain=COALESCE((data->>'uncertain')::boolean,z.uncertain);
   IF after_phy<0 OR jsonb_typeof(after_mat)<>'object' OR (after_phy=0 AND after_mat<>'{}'::jsonb) THEN RAISE EXCEPTION 'Invalid adjusted balance'; END IF;
   IF source_phy<>0 OR source_rem<>'{}'::jsonb THEN
     IF individual OR d.count_id IS NULL OR dest_code IS NOT DISTINCT FROM z.local OR source_rem IS DISTINCT FROM (z.materials::jsonb - ARRAY(SELECT jsonb_array_elements_text(data->'scope_keys')))
       OR source_phy<>(SELECT COALESCE(sum(value::integer),0) FROM jsonb_each_text(source_rem)) THEN RAISE EXCEPTION 'Unproven partial location distribution'; END IF;
   END IF;
   positive=after_phy+source_phy>z.physical_quantity;
   FOR pair IN SELECT * FROM jsonb_each(after_mat) LOOP
     IF pair.value<>'null'::jsonb AND pair.value::text !~ '^[1-9][0-9]{0,9}$' THEN RAISE EXCEPTION 'Invalid material quantity'; END IF;
     IF pair.value<>'null'::jsonb AND (z.materials::jsonb->pair.key IS NULL OR z.materials::jsonb->pair.key='null'::jsonb OR pair.value::text::bigint>(z.materials::jsonb->>pair.key)::bigint) THEN positive=true; END IF;
     IF NOT (known ? pair.key) AND NOT (z.materials::jsonb ? pair.key) THEN RAISE EXCEPTION 'No fictitious lot/article'; END IF;
     IF pair.value='null'::jsonb AND (u.kind<>'lab' OR NOT (z.materials::jsonb ? pair.key)) THEN RAISE EXCEPTION 'Unknown quantity cannot be invented'; END IF;
   END LOOP;
   IF (positive OR (z.uncertain AND NOT after_uncertain)) AND (NOT identifiable OR NOT COALESCE((data->>'count_proven')::boolean,false)) THEN RAISE EXCEPTION 'Positive balance needs proven identity/count'; END IF;
   IF NOT identifiable THEN after_uncertain=true; END IF;
   IF z.uncertain AND NOT after_uncertain THEN
     IF EXISTS(SELECT 1 FROM jsonb_each(after_mat) jm WHERE jm.value='null'::jsonb) OR EXISTS(SELECT 1 FROM jsonb_each(z.materials::jsonb) jm WHERE jm.value='null'::jsonb AND NOT (data->'counted_keys') ? jm.key)
       THEN RAISE EXCEPTION 'Reconciliation has unknown/unverified contents'; END IF;
   END IF;
   IF u.kind IN ('paletes','fio','prateleiras','vasilhame') AND NOT (u.kind='paletes' AND u.physical_quantity=1) AND identifiable
     AND (SELECT COALESCE(sum(value::integer),0) FROM jsonb_each_text(after_mat))<>after_phy THEN RAISE EXCEPTION 'Physical/material totals differ'; END IF;
   IF u.kind IN ('caixas','vazias') OR (u.kind='paletes' AND u.physical_quantity=1) THEN
     IF EXISTS(SELECT 1 FROM jsonb_each_text(after_mat) jm WHERE jm.value::integer<>after_phy) THEN RAISE EXCEPTION 'Lot presence duplicates a physical unit'; END IF;
   END IF;
   IF data->>'basis'='return' THEN
     IF jsonb_typeof(data->'returns')<>'array' OR jsonb_array_length(data->'returns')=0 OR (SELECT count(DISTINCT x->>'line') FROM jsonb_array_elements(data->'returns') x)<>jsonb_array_length(data->'returns') THEN RAISE EXCEPTION 'Shipment compensation reference required'; END IF;
     actual='{}'::jsonb;moved_phy=0;
     FOR claim IN SELECT value AS claim FROM jsonb_array_elements(data->'returns') LOOP
       SELECT l.* INTO line FROM valmet_shipment_lines l JOIN valmet_positions q ON q.id=l.position_id AND q.context_id=l.context_id WHERE l.id=(claim.claim->>'line')::integer AND l.context_id=NEW.context_id AND q.unit_id=u.id;
       IF line.id IS NULL THEN RAISE EXCEPTION 'Compensation has wrong shipment identity'; END IF;
       SELECT COALESCE(sum((a->>'physical')::integer),0) INTO returned FROM valmet_control_moves m JOIN valmet_control_drafts cd ON cd.id=m.draft_id AND cd.context_id=m.context_id,
         LATERAL jsonb_array_elements(COALESCE(cd.payload::jsonb->'returns','[]'::jsonb)) a WHERE m.context_id=NEW.context_id AND (a->>'line')::integer=line.id;
       IF (claim.claim->>'physical')::integer<0 OR returned+(claim.claim->>'physical')::integer>line.physical_quantity THEN RAISE EXCEPTION 'Physical shipment already compensated'; END IF;
       moved_phy=moved_phy+(claim.claim->>'physical')::integer;
       FOR pair IN SELECT * FROM jsonb_each_text(claim.claim->'materials') LOOP
         SELECT COALESCE(sum((a->'materials'->>pair.key)::bigint),0) INTO returned FROM valmet_control_moves m JOIN valmet_control_drafts cd ON cd.id=m.draft_id AND cd.context_id=m.context_id,
           LATERAL jsonb_array_elements(COALESCE(cd.payload::jsonb->'returns','[]'::jsonb)) a WHERE m.context_id=NEW.context_id AND (a->>'line')::integer=line.id;
         IF pair.value::bigint<=0 OR NOT line.materials::jsonb ? pair.key OR returned+pair.value::bigint>(line.materials::jsonb->>pair.key)::bigint THEN RAISE EXCEPTION 'Material shipment already compensated'; END IF;
         actual=jsonb_set(actual,ARRAY[pair.key],to_jsonb(COALESCE((actual->>pair.key)::bigint,0)+pair.value::bigint));
       END LOOP;
     END LOOP;
     IF moved_phy<>GREATEST(0,after_phy+source_phy-z.physical_quantity) THEN RAISE EXCEPTION 'Compensation physical delta mismatch'; END IF;
     FOR pair IN SELECT * FROM jsonb_each(after_mat) LOOP
       IF pair.value='null'::jsonb OR z.materials::jsonb->pair.key='null'::jsonb THEN RAISE EXCEPTION 'Count unknown material before compensation'; END IF;
       delta=GREATEST(0,pair.value::text::bigint-COALESCE((z.materials::jsonb->>pair.key)::bigint,0));
       IF delta<>COALESCE((actual->>pair.key)::bigint,0) THEN RAISE EXCEPTION 'Compensation material delta mismatch'; END IF;
     END LOOP;
     IF EXISTS(SELECT 1 FROM jsonb_object_keys(actual) k WHERE NOT after_mat ? k) THEN RAISE EXCEPTION 'Unexpected compensation material'; END IF;
   ELSIF individual AND after_phy>0 AND (SELECT COALESCE(sum(physical_quantity),0) FROM valmet_positions WHERE context_id=u.context_id AND unit_id=u.id)=0
     AND EXISTS(SELECT 1 FROM valmet_shipment_lines l JOIN valmet_positions q ON q.id=l.position_id AND q.context_id=l.context_id WHERE l.context_id=u.context_id AND q.unit_id=u.id AND l.physical_quantity>0)
       THEN RAISE EXCEPTION 'Returned physical identity must reference original shipment';
   END IF;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(data->'related_lines','[]'::jsonb)) lid WHERE NOT EXISTS(
     SELECT 1 FROM valmet_shipment_lines l JOIN valmet_positions q ON q.id=l.position_id AND q.context_id=l.context_id WHERE l.context_id=u.context_id AND l.id=lid::integer AND q.unit_id=u.id)) THEN RAISE EXCEPTION 'Wrong related shipment reference'; END IF;
   remain=source_rem;
 END IF;
 IF dest_code IS NULL THEN
   IF d.kind='transfer' OR z.local IS NOT NULL OR (after_phy>0 AND NOT (after_phy<z.physical_quantity AND NOT positive AND after_uncertain)) THEN RAISE EXCEPTION 'A real active location is required'; END IF;
 ELSIF NOT EXISTS(SELECT 1 FROM locations WHERE context_id=NEW.context_id AND codigo=dest_code AND active) THEN RAISE EXCEPTION 'A real active location is required'; END IF;
 IF u.kind='vazias' AND dest_code<>'Chão' THEN RAISE EXCEPTION 'Empty boxes must remain on floor'; END IF;
 IF individual AND NOT u.legacy_unnumbered AND after_phy+(SELECT COALESCE(sum(physical_quantity),0) FROM valmet_positions WHERE context_id=u.context_id AND unit_id=u.id AND id<>z.id)>1 THEN RAISE EXCEPTION 'Physical identity duplicated'; END IF;
 IF EXISTS(SELECT 1 FROM valmet_occurrences o WHERE o.context_id=u.context_id AND o.unit_id=u.id AND o.kind<>'resolved'
   AND NOT EXISTS(SELECT 1 FROM valmet_occurrences r WHERE r.resolves_id=o.id AND r.context_id=o.context_id)
   AND NOT COALESCE(data->'resolves','[]'::jsonb) @> to_jsonb(ARRAY[o.id])) THEN RAISE EXCEPTION 'Resolve outstanding occurrences explicitly'; END IF;
 IF d.kind='transfer' THEN
   UPDATE valmet_positions SET physical_quantity=physical_quantity-moved_phy,materials=remain::text WHERE id=z.id;
 ELSE UPDATE valmet_positions SET physical_quantity=source_phy,materials=source_rem::text WHERE id=z.id;
 END IF;
 SELECT id,physical_quantity INTO dest,dest_phy FROM valmet_positions WHERE context_id=u.context_id AND unit_id=u.id AND local IS NOT DISTINCT FROM dest_code;
 IF dest IS NULL THEN INSERT INTO valmet_positions(context_id,unit_id,root,local,physical_quantity,materials,uncertain) VALUES(u.context_id,u.id,false,dest_code,0,'{}',false) RETURNING id INTO dest;dest_phy=0; END IF;
 SELECT capacidade INTO cap FROM locations WHERE context_id=u.context_id AND codigo=dest_code;
 SELECT COALESCE(sum(physical_quantity),0)+(SELECT COALESCE(sum(quantidade),0) FROM stock WHERE context_id=u.context_id AND local=dest_code) INTO used FROM valmet_positions WHERE context_id=u.context_id AND local IS NOT DISTINCT FROM dest_code;
 IF cap IS NOT NULL AND used+after_phy>cap THEN RAISE EXCEPTION 'Physical capacity exceeded'; END IF;
 UPDATE valmet_positions SET physical_quantity=physical_quantity+after_phy,uncertain=CASE WHEN dest_phy=0 THEN after_uncertain ELSE uncertain OR after_uncertain END WHERE id=dest;
 FOR pair IN SELECT * FROM jsonb_each(after_mat) LOOP
   UPDATE valmet_positions SET materials=jsonb_set(materials::jsonb,ARRAY[pair.key],CASE WHEN pair.value='null'::jsonb THEN 'null'::jsonb ELSE to_jsonb(COALESCE((materials::jsonb->>pair.key)::bigint,0)+pair.value::text::bigint) END)::text WHERE id=dest;
 END LOOP;
 IF d.kind='adjustment' THEN
   FOR resolved IN SELECT value::integer AS id FROM jsonb_array_elements_text(COALESCE(data->'resolves','[]'::jsonb)) LOOP
     SELECT * INTO c FROM valmet_occurrences WHERE id=resolved.id AND context_id=u.context_id AND unit_id=u.id AND kind<>'resolved';
     IF c.id IS NULL THEN RAISE EXCEPTION 'Resolution refers to another unit/context'; END IF;
     IF NOT EXISTS(SELECT 1 FROM valmet_occurrences WHERE resolves_id=c.id AND context_id=c.context_id) THEN
       INSERT INTO valmet_occurrences(context_id,preparation_id,unit_id,version,kind,request_key,resolves_id,details,actor,created_at)
         VALUES(c.context_id,c.preparation_id,c.unit_id,c.version,'resolved',NULL,c.id,jsonb_build_object('control_move',NEW.id,'proof',data->>'evidence')::text,NEW.actor,NEW.created_at);
     END IF;
   END LOOP;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_control_projection BEFORE INSERT ON valmet_control_moves FOR EACH ROW EXECUTE FUNCTION valmet_apply_control();
CREATE CONSTRAINT TRIGGER valmet_control_reservations AFTER INSERT ON valmet_control_moves DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION valmet_reservation_totals();

CREATE FUNCTION valmet_control_count_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF NOT EXISTS(SELECT 1 FROM valmet_inventories WHERE id=NEW.inventory_id AND context_id=NEW.context_id AND status<>'closed')
   OR NEW.kind NOT IN ('count','unknown','omit') OR (NEW.kind='omit' AND NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role='admin'))
   THEN RAISE EXCEPTION 'Invalid inventory evidence'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_count_scope BEFORE INSERT ON valmet_counts FOR EACH ROW EXECUTE FUNCTION valmet_control_count_guard();
CREATE FUNCTION valmet_control_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c record; d record; z record; data jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO c FROM valmet_counts WHERE id=NEW.count_id AND context_id=NEW.context_id AND inventory_id=NEW.inventory_id;
 IF c.id IS NULL OR NEW.kind NOT IN ('approve','defer','omit') OR btrim(NEW.reason)='' OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role='admin')
   OR NOT EXISTS(SELECT 1 FROM valmet_inventories WHERE id=NEW.inventory_id AND context_id=NEW.context_id AND status='submitted')
   OR c.id<>(SELECT max(id) FROM valmet_counts WHERE context_id=NEW.context_id AND inventory_id=c.inventory_id AND target=c.target)
   THEN RAISE EXCEPTION 'Invalid inventory decision'; END IF;
 IF NEW.kind='approve' THEN
   data=c.payload::jsonb;
   IF c.kind<>'count' OR NOT (data->>'complete')::boolean THEN RAISE EXCEPTION 'Incomplete inventory count'; END IF;
   IF NEW.move_id IS NOT NULL THEN
     IF NOT EXISTS(SELECT 1 FROM valmet_control_moves m JOIN valmet_control_drafts draft ON draft.id=m.draft_id AND draft.context_id=m.context_id
       WHERE m.id=NEW.move_id AND m.context_id=NEW.context_id AND draft.count_id=c.id AND draft.inventory_id=NEW.inventory_id) THEN RAISE EXCEPTION 'Unrelated reconciliation movement'; END IF;
   ELSIF data->'target'->>'position' IS NOT NULL THEN
     SELECT * INTO z FROM valmet_positions WHERE id=(data->'target'->>'position')::integer AND context_id=NEW.context_id;
     IF c.reference::jsonb IS DISTINCT FROM valmet_control_reference(z.id,NEW.context_id,data->'after'->>'target',NEW.inventory_id)
       OR z.physical_quantity<>(data->'after'->>'physical')::integer OR z.materials::jsonb IS DISTINCT FROM data->'after'->'materials'
       OR z.local IS DISTINCT FROM data->'after'->>'target' THEN RAISE EXCEPTION 'Difference requires a reconciliation movement'; END IF;
   END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER valmet_decision_scope BEFORE INSERT ON valmet_inventory_decisions FOR EACH ROW EXECUTE FUNCTION valmet_control_decision_guard();

CREATE OR REPLACE FUNCTION valmet_content_projection() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF TG_RELID<>'valmet_items'::regclass OR TG_OP<>'INSERT' OR TG_WHEN<>'AFTER' THEN RAISE EXCEPTION 'Invalid projection trigger source'; END IF;
 IF NEW.identification_id IS NOT NULL THEN
   IF NOT EXISTS(SELECT 1 FROM valmet_identifications WHERE id=NEW.identification_id AND context_id=NEW.context_id AND xmin=pg_current_xact_id()::xid)
      OR EXISTS(SELECT 1 FROM valmet_relocations m JOIN valmet_positions z ON z.id=m.position_id AND z.context_id=m.context_id WHERE z.unit_id=NEW.unit_id AND z.context_id=NEW.context_id)
      OR EXISTS(SELECT 1 FROM valmet_shipment_lines m JOIN valmet_positions z ON z.id=m.position_id AND z.context_id=m.context_id WHERE z.unit_id=NEW.unit_id AND z.context_id=NEW.context_id)
      THEN RAISE EXCEPTION 'Confirmed contents cannot be appended or reset'; END IF;
   IF EXISTS(SELECT 1 FROM valmet_control_moves WHERE unit_id=NEW.unit_id AND context_id=NEW.context_id) THEN
     UPDATE valmet_positions SET uncertain=true WHERE unit_id=NEW.unit_id AND context_id=NEW.context_id;
   ELSE UPDATE valmet_positions SET materials=valmet_original_materials(NEW.unit_id,NEW.context_id)::text WHERE unit_id=NEW.unit_id AND context_id=NEW.context_id AND root;
   END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE TRIGGER valmet_control_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_counts FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_control_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_control_moves FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_control_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_inventory_decisions FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER valmet_control_history BEFORE UPDATE OR DELETE OR TRUNCATE ON valmet_control_documents FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
