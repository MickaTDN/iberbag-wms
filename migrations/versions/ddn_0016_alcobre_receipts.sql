CREATE FUNCTION alcobre_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE owner integer; e record; r record; a record; zone_record record; body jsonb; item jsonb; qty integer; used integer; who record; unit record;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT c.client_id INTO owner FROM storage_contexts c JOIN alcobre_owners o ON o.client_id=c.client_id AND o.warehouse_id=c.warehouse_id JOIN clients cl ON cl.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND cl.active AND w.active;
 IF owner IS NULL THEN RAISE EXCEPTION 'Alcobre context required'; END IF;
 IF TG_TABLE_NAME='alcobre_zones' THEN
  IF NEW.code !~ '^[A-Z0-9]{1,20}$' THEN RAISE EXCEPTION 'Invalid zone'; END IF;
  IF TG_OP='UPDATE' AND (NEW.id<>OLD.id OR NEW.context_id<>OLD.context_id OR EXISTS(SELECT 1 FROM alcobre_slots s WHERE s.zone_id=OLD.id AND s.context_id=OLD.context_id AND (s.position>NEW.positions OR (NOT NEW.active AND EXISTS(SELECT 1 FROM alcobre_placements p WHERE p.local=s.local AND p.context_id=s.context_id))))) THEN RAISE EXCEPTION 'Zone has physical assignments or stock'; END IF;
  RETURN NEW;
 ELSIF TG_TABLE_NAME='alcobre_slots' THEN
  IF TG_OP='UPDATE' AND (NEW.id<>OLD.id OR NEW.context_id<>OLD.context_id OR NEW.zone_id<>OLD.zone_id OR NEW.position<>OLD.position OR EXISTS(SELECT 1 FROM alcobre_placements WHERE context_id=OLD.context_id AND local=OLD.local)) THEN RAISE EXCEPTION 'Occupied or historical physical assignment immutable'; END IF;
  SELECT * INTO zone_record FROM alcobre_zones WHERE id=NEW.zone_id AND context_id=NEW.context_id AND active;
  IF zone_record.id IS NULL OR NEW.position NOT BETWEEN 1 AND zone_record.positions OR NOT EXISTS(SELECT 1 FROM locations l JOIN physical_location_claims p ON p.context_id=l.context_id AND p.code=lower(btrim(l.codigo)) WHERE l.context_id=NEW.context_id AND l.codigo=NEW.local AND l.active AND l.capacidade=1) OR lower(NEW.local) IN ('chão','chao','por localizar') THEN RAISE EXCEPTION 'Explicit active physical position required'; END IF;
  RETURN NEW;
 ELSIF TG_TABLE_NAME='alcobre_entries' THEN
  IF TG_OP='INSERT' AND NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role IN ('admin','operador')) THEN RAISE EXCEPTION 'Active actor required'; END IF;
  IF NEW.source_pdf IS NOT NULL AND (octet_length(NEW.source_pdf)>5242880 OR NEW.source_digest IS DISTINCT FROM encode(sha256(NEW.source_pdf),'hex') OR NEW.source_name IS NULL OR NEW.mode<>'direct' OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND role='admin')) THEN RAISE EXCEPTION 'Invalid PDF source'; END IF;
  IF TG_OP='INSERT' AND (NEW.status<>'draft' OR NEW.version<>0 OR NEW.quantity IS NOT NULL OR NEW.confirmed_at IS NOT NULL OR NEW.confirmed_by IS NOT NULL) THEN RAISE EXCEPTION 'Draft receipt required'; END IF;
  IF TG_OP='UPDATE' THEN
   IF OLD.status='confirmed' OR (to_jsonb(NEW)-ARRAY['version','status','quantity','confirmed_by','confirmed_at','reference','received_on']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['version','status','quantity','confirmed_by','confirmed_at','reference','received_on']) THEN RAISE EXCEPTION 'Receipt source immutable'; END IF;
   IF NEW.status='confirmed' THEN
    SELECT rv.*,act.actor AS confirmer INTO r FROM alcobre_reviews rv JOIN alcobre_actions act ON act.review_id=rv.id AND act.context_id=rv.context_id WHERE rv.entry_id=NEW.id AND rv.context_id=NEW.context_id AND rv.kind='receive' AND rv.version=NEW.version;
    IF r.id IS NULL OR NEW.quantity IS DISTINCT FROM (r.payload::jsonb->>'quantity')::integer OR NEW.confirmed_by IS DISTINCT FROM r.confirmer OR NEW.confirmed_at IS NULL OR NEW.reference IS DISTINCT FROM (r.payload::jsonb->>'reference') OR NEW.received_on IS DISTINCT FROM (r.payload::jsonb->>'received_on') THEN RAISE EXCEPTION 'Confirmed receipt must match action'; END IF;
   ELSIF NEW.reference IS DISTINCT FROM OLD.reference OR NEW.received_on IS DISTINCT FROM OLD.received_on OR NEW.status<>'draft' OR NEW.quantity IS NOT NULL OR NEW.confirmed_by IS NOT NULL OR NEW.confirmed_at IS NOT NULL OR NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Invalid draft change'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='alcobre_placements' THEN
  SELECT * INTO unit FROM alcobre_bobines WHERE id=NEW.bobine_id AND context_id=NEW.context_id;
  SELECT * INTO a FROM alcobre_actions WHERE id=NEW.action_id AND context_id=NEW.context_id;
  IF unit.id IS NULL OR a.id IS NULL OR a.entry_id<>unit.entry_id THEN RAISE EXCEPTION 'Placement identity mismatch'; END IF;
  SELECT * INTO r FROM alcobre_reviews WHERE id=a.review_id AND context_id=NEW.context_id;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.payload::jsonb->'rows') x WHERE x->>'number'=unit.number AND x->>'local'=NEW.local) THEN RAISE EXCEPTION 'Placement not reviewed'; END IF;
  IF NOT EXISTS(SELECT 1 FROM alcobre_slots s JOIN alcobre_zones z ON z.id=s.zone_id AND z.context_id=s.context_id JOIN locations l ON l.context_id=s.context_id AND l.codigo=s.local WHERE s.context_id=NEW.context_id AND s.local=NEW.local AND z.active AND l.active AND l.capacidade=1) THEN RAISE EXCEPTION 'Invalid position'; END IF;
  SELECT count(*) INTO used FROM alcobre_placements WHERE local=NEW.local AND context_id=NEW.context_id;
  used=used+(SELECT coalesce(sum(quantidade),0) FROM stock WHERE local=NEW.local AND context_id=NEW.context_id)+(SELECT coalesce(sum(physical_quantity),0) FROM valmet_positions WHERE local=NEW.local AND context_id=NEW.context_id)+(SELECT count(*) FROM nkt_pallets p WHERE p.local=NEW.local AND p.context_id=NEW.context_id AND nkt_current_state(p.id) NOT IN ('shipped','missing'));
  IF used<>0 THEN RAISE EXCEPTION 'One bobine per position'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO e FROM alcobre_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id;
 IF e.id IS NULL THEN RAISE EXCEPTION 'Receipt required'; END IF;
 IF TG_TABLE_NAME IN ('alcobre_reviews','alcobre_actions') THEN
  SELECT * INTO who FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador');
  IF who.id IS NULL OR (who.id<>e.created_by AND who.role<>'admin') THEN RAISE EXCEPTION 'Actor not permitted'; END IF;
 END IF;
 SELECT count(*) INTO used FROM alcobre_bobines WHERE entry_id=e.id AND context_id=NEW.context_id;
 IF TG_TABLE_NAME='alcobre_reviews' THEN
  body=NEW.payload::jsonb;
  IF NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') OR btrim(NEW.reason)='' OR NEW.version<>e.version OR body->>'kind' IS DISTINCT FROM NEW.kind OR coalesce(jsonb_typeof(body->'rows'),'null')<>'array' OR coalesce(jsonb_typeof(body->'quantity'),'null')<>'number' OR coalesce(jsonb_typeof(body->'identified_before'),'null')<>'number' OR (body->>'identified_before')::integer<>used THEN RAISE EXCEPTION 'Explicit matching review required'; END IF;
  qty=(body->>'quantity')::integer;
  IF qty NOT BETWEEN 1 AND 300 OR jsonb_array_length(body->'rows')>300 THEN RAISE EXCEPTION 'Invalid physical quantity'; END IF;
  IF NEW.kind='receive' THEN
   IF e.status<>'draft' OR (e.mode='pending' AND jsonb_array_length(body->'rows')<>0) OR (e.mode='direct' AND jsonb_array_length(body->'rows')<>qty) THEN RAISE EXCEPTION 'Receipt rows mismatch'; END IF;
   IF e.source_pdf IS NOT NULL AND coalesce(body->>'manual','false')<>'true' AND (coalesce(e.extraction::jsonb->>'reliable','false')<>'true' OR (SELECT jsonb_agg(x->>'number' ORDER BY ord) FROM jsonb_array_elements(body->'rows') WITH ORDINALITY ar(x,ord)) IS DISTINCT FROM (SELECT jsonb_agg(x->>'number' ORDER BY ord) FROM jsonb_array_elements(e.extraction::jsonb->'candidates') WITH ORDINALITY ar(x,ord))) THEN RAISE EXCEPTION 'Unreliable PDF requires evidenced manual transcription'; END IF;
  ELSE
   IF e.status<>'confirmed' OR qty<>e.quantity OR jsonb_array_length(body->'rows')<1 OR (NEW.kind='identify' AND (e.mode<>'pending' OR used+jsonb_array_length(body->'rows')>qty)) THEN RAISE EXCEPTION 'Identification exceeds pending material'; END IF;
  END IF;
  FOR item IN SELECT * FROM jsonb_array_elements(body->'rows') LOOP
   IF coalesce(jsonb_typeof(item->'number'),'null')<>'string' OR length(item->>'number') NOT BETWEEN 1 AND 120 OR btrim(item->>'number')='' OR item->>'number'<>btrim(item->>'number') OR item->>'number' ~ '[[:cntrl:]]' THEN RAISE EXCEPTION 'Real number required'; END IF;
   IF NEW.kind='place' AND (coalesce(item->>'local','')='' OR NOT EXISTS(SELECT 1 FROM alcobre_bobines b WHERE b.context_id=NEW.context_id AND b.entry_id=e.id AND b.number=item->>'number' AND NOT EXISTS(SELECT 1 FROM alcobre_placements p WHERE p.context_id=b.context_id AND p.bobine_id=b.id))) THEN RAISE EXCEPTION 'Only unlocated original bobines may be placed'; END IF;
   IF NEW.kind<>'place' AND EXISTS(SELECT 1 FROM alcobre_bobines WHERE client_id=owner AND lower(number)=lower(item->>'number')) THEN RAISE EXCEPTION 'Duplicate identity'; END IF;
  END LOOP;
  IF (SELECT count(*) FROM jsonb_array_elements(body->'rows'))<>(SELECT count(DISTINCT lower(x->>'number')) FROM jsonb_array_elements(body->'rows') x) THEN RAISE EXCEPTION 'Duplicate reviewed number'; END IF;
 ELSIF TG_TABLE_NAME='alcobre_actions' THEN
  SELECT * INTO r FROM alcobre_reviews WHERE id=NEW.review_id AND context_id=NEW.context_id AND entry_id=e.id;
  IF r.id IS NULL OR r.version<>e.version OR (r.payload::jsonb->>'identified_before')::integer<>used OR (r.kind='receive' AND e.status<>'draft') OR (r.kind<>'receive' AND e.status<>'confirmed') THEN RAISE EXCEPTION 'Stale or mismatched review'; END IF;
 ELSIF TG_TABLE_NAME='alcobre_bobines' THEN
  SELECT * INTO a FROM alcobre_actions WHERE id=NEW.action_id AND context_id=NEW.context_id AND entry_id=e.id;
  SELECT * INTO r FROM alcobre_reviews WHERE id=a.review_id AND context_id=NEW.context_id;
  IF a.id IS NULL OR r.kind NOT IN ('receive','identify') OR NEW.client_id<>owner OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(r.payload::jsonb->'rows') x WHERE x->>'number'=NEW.number) OR used >= (r.payload::jsonb->>'quantity')::integer OR NEW.number_key<>lower(NEW.number) AND NEW.number ~ '^[ -~]+$' THEN RAISE EXCEPTION 'Bobine must belong to original reviewed material'; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE FUNCTION alcobre_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE r record; e record; expected integer; actual integer;
BEGIN
 SELECT * INTO r FROM alcobre_reviews WHERE id=NEW.review_id AND context_id=NEW.context_id;
 SELECT * INTO e FROM alcobre_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id;
 IF e.status<>'confirmed' THEN RAISE EXCEPTION 'Receipt must finish atomically'; END IF;
 expected=jsonb_array_length(r.payload::jsonb->'rows');
 SELECT count(*) INTO actual FROM alcobre_bobines WHERE action_id=NEW.id AND context_id=NEW.context_id;
 IF (r.kind='place' AND actual<>0) OR (r.kind<>'place' AND actual<>expected) THEN RAISE EXCEPTION 'Incomplete identity operation'; END IF;
 SELECT count(*) INTO actual FROM alcobre_placements WHERE action_id=NEW.id AND context_id=NEW.context_id;
 IF actual<>(SELECT count(*) FROM jsonb_array_elements(r.payload::jsonb->'rows') x WHERE x->>'local' IS NOT NULL) THEN RAISE EXCEPTION 'Incomplete placement operation'; END IF;
 IF (SELECT count(*) FROM alcobre_bobines WHERE entry_id=e.id AND context_id=e.context_id)>e.quantity THEN RAISE EXCEPTION 'Physical stock duplicated'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION alcobre_location_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF EXISTS(SELECT 1 FROM alcobre_slots WHERE context_id=OLD.context_id AND local=OLD.codigo) THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Alcobre physical position assigned'; END IF;
  IF NEW.context_id<>OLD.context_id OR NEW.codigo<>OLD.codigo OR NEW.capacidade IS DISTINCT FROM 1 OR (NOT NEW.active AND EXISTS(SELECT 1 FROM alcobre_placements WHERE context_id=OLD.context_id AND local=OLD.codigo)) THEN RAISE EXCEPTION 'Alcobre position capacity/occupancy protected'; END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;RETURN NEW;
END $$;
CREATE TRIGGER alcobre_locations BEFORE UPDATE OR DELETE ON locations FOR EACH ROW EXECUTE FUNCTION alcobre_location_guard();
CREATE TRIGGER alcobre_zones_guard BEFORE INSERT OR UPDATE ON alcobre_zones FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE TRIGGER alcobre_slots_guard BEFORE INSERT OR UPDATE ON alcobre_slots FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE TRIGGER alcobre_entries_guard BEFORE INSERT OR UPDATE ON alcobre_entries FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE TRIGGER alcobre_reviews_guard BEFORE INSERT ON alcobre_reviews FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE TRIGGER alcobre_actions_guard BEFORE INSERT ON alcobre_actions FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE TRIGGER alcobre_bobines_guard BEFORE INSERT ON alcobre_bobines FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE TRIGGER alcobre_placements_guard BEFORE INSERT ON alcobre_placements FOR EACH ROW EXECUTE FUNCTION alcobre_guard();
CREATE CONSTRAINT TRIGGER alcobre_atomic AFTER INSERT ON alcobre_actions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION alcobre_final();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['alcobre_owners','alcobre_reviews','alcobre_actions','alcobre_bobines','alcobre_placements'] LOOP
  EXECUTE format('CREATE TRIGGER %%I BEFORE UPDATE OR DELETE OR TRUNCATE ON %%I FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable()',t||'_immutable',t);
 END LOOP;
END $$;
CREATE TRIGGER alcobre_entry_delete BEFORE DELETE OR TRUNCATE ON alcobre_entries FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER alcobre_zone_delete BEFORE DELETE OR TRUNCATE ON alcobre_zones FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();

CREATE FUNCTION alcobre_foreign_stock() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF NEW.quantidade>0 AND EXISTS(SELECT 1 FROM storage_contexts c JOIN alcobre_owners a ON a.client_id=c.client_id WHERE c.id=NEW.context_id) THEN RAISE EXCEPTION 'Alcobre requires individual bobines, not grouped stock'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER alcobre_no_grouped_stock BEFORE INSERT OR UPDATE ON stock FOR EACH ROW EXECUTE FUNCTION alcobre_foreign_stock();

CREATE TRIGGER alcobre_slot_delete BEFORE DELETE OR TRUNCATE ON alcobre_slots FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
