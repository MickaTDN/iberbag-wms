CREATE FUNCTION nkt_current_state(p integer) RETURNS text LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT coalesce((SELECT state FROM nkt_stock_events WHERE pallet_id=p AND context_id=(SELECT context_id FROM nkt_pallets WHERE id=p) ORDER BY id DESC LIMIT 1),'available')
$$;
CREATE FUNCTION nkt_current_preparation(p integer) RETURNS integer LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT o.preparation_id FROM nkt_stock_events e JOIN nkt_operations o ON o.id=e.operation_id AND o.context_id=e.context_id WHERE e.pallet_id=p AND e.context_id=(SELECT context_id FROM nkt_pallets WHERE id=p) ORDER BY e.id DESC LIMIT 1
$$;
CREATE FUNCTION nkt_loading_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE prep record;rev record;op record;prior record;fix record;original record;cap integer;used bigint;pid integer;oldstate text;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF TG_TABLE_NAME='nkt_preparations' THEN
  IF TG_OP='DELETE' THEN
   IF OLD.status<>'draft' THEN RAISE EXCEPTION 'NKT preparation history retained'; END IF;RETURN OLD;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN nkt_owners o ON o.client_id=c.client_id JOIN clients a ON a.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND a.active AND w.active AND w.name='Escapães') THEN RAISE EXCEPTION 'NKT Escapães required'; END IF;
  IF NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role IN ('admin','operador')) THEN RAISE EXCEPTION 'Active author required'; END IF;
  IF TG_OP='INSERT' AND (NEW.version<>1 OR NEW.active_revision IS NOT NULL OR NEW.status<>'draft') THEN RAISE EXCEPTION 'Draft required'; END IF;
  IF TG_OP='UPDATE' THEN
   IF ROW(NEW.context_id,NEW.request_key,NEW.created_by,NEW.created_at) IS DISTINCT FROM ROW(OLD.context_id,OLD.request_key,OLD.created_by,OLD.created_at) OR OLD.status='cancelled' OR NEW.version NOT BETWEEN OLD.version AND OLD.version+1 THEN RAISE EXCEPTION 'Immutable identity/cancelled preparation'; END IF;
   IF NEW.active_revision IS NOT NULL AND NOT EXISTS(SELECT 1 FROM nkt_plan_revisions WHERE id=NEW.active_revision AND preparation_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Invalid active revision'; END IF;
  END IF;RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='nkt_stock_events' THEN
  SELECT * INTO op FROM nkt_operations WHERE id=NEW.operation_id AND context_id=NEW.context_id;
  SELECT * INTO prior FROM nkt_stock_events WHERE pallet_id=NEW.pallet_id AND context_id=NEW.context_id ORDER BY id DESC LIMIT 1;
  IF op.id IS NULL OR NEW.previous_event_id IS DISTINCT FROM prior.id THEN RAISE EXCEPTION 'Stale PEG event'; END IF;
  oldstate=coalesce(prior.state,'available');
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'events') x WHERE (x->>'pallet_id')::integer=NEW.pallet_id AND (x->>'previous_event_id')::integer IS NOT DISTINCT FROM NEW.previous_event_id AND x->>'state'=NEW.state) THEN RAISE EXCEPTION 'Event outside reviewed operation'; END IF;
  IF NOT EXISTS(SELECT 1 FROM nkt_pallets p JOIN nkt_entries e ON e.id=p.entry_id AND e.context_id=p.context_id WHERE p.id=NEW.pallet_id AND p.context_id=NEW.context_id AND e.status='confirmed') THEN RAISE EXCEPTION 'Confirmed PEG required'; END IF;
  IF op.kind='reserve' THEN
   IF NEW.state='reserved' THEN
    IF oldstate<>'available' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'plan'->'items') x WHERE (x->>'pallet_id')::integer=NEW.pallet_id) THEN RAISE EXCEPTION 'PEG unavailable/unrequested'; END IF;
   ELSIF NEW.state='available' THEN
    IF oldstate<>'reserved' OR NOT EXISTS(SELECT 1 FROM nkt_operations WHERE id=prior.operation_id AND preparation_id=op.preparation_id) OR EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'plan'->'items') x WHERE (x->>'pallet_id')::integer=NEW.pallet_id) THEN RAISE EXCEPTION 'Invalid explicit release'; END IF;
   ELSE RAISE EXCEPTION 'Reservation cannot ship'; END IF;
  ELSIF op.kind IN ('ship','cancel') THEN
   IF oldstate<>'reserved' OR NOT EXISTS(SELECT 1 FROM nkt_operations WHERE id=prior.operation_id AND preparation_id=op.preparation_id) OR NEW.state<>(CASE WHEN op.kind='ship' THEN 'shipped' ELSE 'available' END) THEN RAISE EXCEPTION 'PEG not exclusively reserved by preparation'; END IF;
   IF op.kind='ship' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'plan'->'items') x WHERE (x->>'pallet_id')::integer=NEW.pallet_id) THEN RAISE EXCEPTION 'Unplanned shipment'; END IF;
  ELSIF op.kind='restore' THEN
   IF oldstate<>'shipped' OR prior.operation_id IS DISTINCT FROM op.corrects_id OR NEW.state<>'available' THEN RAISE EXCEPTION 'Unsafe or duplicated compensation'; END IF;
   SELECT l.capacidade INTO cap FROM locations l JOIN nkt_pallets p ON p.context_id=l.context_id AND p.local=l.codigo WHERE p.id=NEW.pallet_id AND p.context_id=NEW.context_id AND l.active;
   IF NOT FOUND THEN RAISE EXCEPTION 'Restoration location missing/inactive'; END IF;
   SELECT count(*) INTO used FROM nkt_pallets p WHERE p.context_id=NEW.context_id AND p.local=(SELECT local FROM nkt_pallets WHERE id=NEW.pallet_id) AND nkt_current_state(p.id)<>'shipped';
   used=used+(SELECT coalesce(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=(SELECT local FROM nkt_pallets WHERE id=NEW.pallet_id))+(SELECT coalesce(sum(physical_quantity),0) FROM valmet_positions WHERE context_id=NEW.context_id AND local=(SELECT local FROM nkt_pallets WHERE id=NEW.pallet_id));
   IF cap IS NOT NULL AND used+1>cap THEN RAISE EXCEPTION 'Restoration exceeds physical capacity'; END IF;
   IF EXISTS(SELECT 1 FROM physical_location_claims f JOIN storage_contexts c ON c.warehouse_id=f.warehouse_id JOIN nkt_pallets p ON p.context_id=c.id AND p.id=NEW.pallet_id WHERE c.id=NEW.context_id AND f.code=lower(btrim(p.local)) AND f.context_id<>NEW.context_id) THEN RAISE EXCEPTION 'Conflicting physical ownership'; END IF;
  ELSE RAISE EXCEPTION 'Metadata correction cannot move stock'; END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='nkt_documents' THEN
  IF NEW.digest<>encode(sha256(NEW.pdf),'hex') OR octet_length(NEW.pdf)>4194304 OR substring(NEW.pdf from 1 for 5)<>convert_to(chr(37)||'PDF-','UTF8') THEN RAISE EXCEPTION 'Invalid immutable PDF'; END IF;
  IF NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador')) THEN RAISE EXCEPTION 'Active actor required'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO prep FROM nkt_preparations WHERE id=NEW.preparation_id AND context_id=NEW.context_id;
 IF prep.id IS NULL OR NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN nkt_owners o ON o.client_id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id JOIN clients a ON a.id=c.client_id WHERE c.id=NEW.context_id AND c.active AND a.active AND w.active AND w.name='Escapães') OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador') AND (id=prep.created_by OR role='admin')) THEN RAISE EXCEPTION 'NKT context/author required'; END IF;
 IF TG_TABLE_NAME='nkt_plan_revisions' THEN
  IF prep.status IN ('cancelled','loaded') OR NEW.version<>prep.version OR btrim(NEW.reason)='' OR NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') OR jsonb_typeof(NEW.payload::jsonb->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.payload::jsonb->'items') NOT BETWEEN 1 AND 60 THEN RAISE EXCEPTION 'Invalid review'; END IF;
  IF (SELECT count(DISTINCT (x->>'pallet_id')::integer) FROM jsonb_array_elements(NEW.payload::jsonb->'items') x)<>jsonb_array_length(NEW.payload::jsonb->'items') THEN RAISE EXCEPTION 'Duplicate physical PEG in plan'; END IF;
  IF coalesce(btrim(NEW.payload::jsonb->>'reference'),'')='' OR coalesce(NEW.payload::jsonb->>'planned_on','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN RAISE EXCEPTION 'Explicit reference/date required'; END IF;
  PERFORM (NEW.payload::jsonb->>'planned_on')::date;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.payload::jsonb->'items') x LEFT JOIN nkt_pallets p ON p.id=(x->>'pallet_id')::integer AND p.context_id=NEW.context_id WHERE p.id IS NULL OR p.peg IS DISTINCT FROM x->>'peg' OR p.local IS DISTINCT FROM x->>'local' OR (x->>'container') IS DISTINCT FROM (SELECT name FROM nkt_containers WHERE context_id=NEW.context_id AND entry_id=p.entry_id) OR (x->>'material') IS DISTINCT FROM (SELECT i->>'material' FROM nkt_containers c,LATERAL jsonb_array_elements(c.payload::jsonb->'items') i WHERE c.context_id=NEW.context_id AND c.entry_id=p.entry_id AND i->>'peg'=p.peg)) THEN RAISE EXCEPTION 'Original PEG/material/container mismatch'; END IF;
 ELSIF TG_TABLE_NAME='nkt_corrections' THEN
  SELECT * INTO original FROM nkt_operations WHERE id=NEW.operation_id AND preparation_id=prep.id AND context_id=NEW.context_id AND kind='ship';
  IF original.id IS NULL OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role='admin') OR NEW.digest<>encode(sha256(convert_to(NEW.snapshot,'UTF8')),'hex') OR coalesce(btrim(NEW.payload::jsonb->>'reason'),'')='' THEN RAISE EXCEPTION 'Administrative correction proof required'; END IF;
 ELSE
  SELECT * INTO rev FROM nkt_plan_revisions WHERE id=NEW.revision_id AND preparation_id=prep.id AND context_id=NEW.context_id;
  IF rev.id IS NULL OR rev.version<>prep.version OR (NEW.snapshot::jsonb->>'before_operation')::integer IS DISTINCT FROM (SELECT max(id) FROM nkt_operations WHERE preparation_id=prep.id AND context_id=NEW.context_id) OR (NEW.snapshot::jsonb->'plan') IS DISTINCT FROM rev.payload::jsonb OR jsonb_typeof(NEW.snapshot::jsonb->'events') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'Stale operation/review'; END IF;
  IF NEW.kind='ship' THEN
   IF prep.status NOT IN ('reserved','partial') OR prep.active_revision<>NEW.revision_id OR coalesce(btrim(NEW.snapshot::jsonb->>'gt'),'')='' OR coalesce(NEW.snapshot::jsonb->>'loaded_on','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR jsonb_array_length(NEW.snapshot::jsonb->'events')<1 THEN RAISE EXCEPTION 'Explicit reserved PEG/date/GT required'; END IF;
   PERFORM (NEW.snapshot::jsonb->>'loaded_on')::date;
  ELSIF NEW.kind='reserve' THEN
   IF prep.status IN ('cancelled','loaded') THEN RAISE EXCEPTION 'Preparation terminal'; END IF;
  ELSIF NEW.kind='cancel' THEN
   IF prep.status IN ('loaded','cancelled') OR coalesce(btrim(NEW.payload::jsonb->>'reason'),'')='' THEN RAISE EXCEPTION 'Invalid cancellation'; END IF;
  ELSE
   SELECT * INTO fix FROM nkt_corrections WHERE id=NEW.correction_id AND preparation_id=prep.id AND operation_id=NEW.corrects_id AND context_id=NEW.context_id;
   IF fix.id IS NULL OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role='admin') OR fix.snapshot::jsonb IS DISTINCT FROM NEW.snapshot::jsonb->'effects' OR NEW.kind<>fix.payload::jsonb->>'kind' THEN RAISE EXCEPTION 'Unreviewed administrative compensation'; END IF;
   IF NEW.kind='metadata' AND jsonb_array_length(NEW.snapshot::jsonb->'events')<>0 THEN RAISE EXCEPTION 'Metadata cannot affect stock'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION nkt_loading_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE recordrow record;sent integer;total integer;
BEGIN
 IF TG_TABLE_NAME='nkt_operations' THEN
  IF (SELECT count(*) FROM nkt_stock_events WHERE operation_id=NEW.id AND context_id=NEW.context_id)<>jsonb_array_length(NEW.snapshot::jsonb->'events') OR NOT EXISTS(SELECT 1 FROM nkt_documents WHERE operation_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Incomplete NKT operation/document'; END IF;
  IF NEW.id=(SELECT max(id) FROM nkt_operations WHERE preparation_id=NEW.preparation_id AND context_id=NEW.context_id) THEN
   SELECT * INTO recordrow FROM nkt_preparations WHERE id=NEW.preparation_id AND context_id=NEW.context_id;
   IF NEW.kind IN ('reserve','ship') THEN
    IF recordrow.active_revision IS DISTINCT FROM NEW.revision_id OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.snapshot::jsonb->'plan'->'items') x WHERE nkt_current_state((x->>'pallet_id')::integer) NOT IN ('reserved','shipped') OR nkt_current_preparation((x->>'pallet_id')::integer) IS DISTINCT FROM NEW.preparation_id) THEN RAISE EXCEPTION 'Incomplete exclusive reservations'; END IF;
    IF EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.context_id=NEW.context_id AND nkt_current_state(p.id)='reserved' AND nkt_current_preparation(p.id)=NEW.preparation_id AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.snapshot::jsonb->'plan'->'items') x WHERE (x->>'pallet_id')::integer=p.id)) THEN RAISE EXCEPTION 'Abandoned reservations'; END IF;
    SELECT count(*),count(*) FILTER (WHERE nkt_current_state((x->>'pallet_id')::integer)='shipped') INTO total,sent FROM jsonb_array_elements(NEW.snapshot::jsonb->'plan'->'items') x;
    IF recordrow.status IS DISTINCT FROM (CASE WHEN sent=total THEN 'loaded' WHEN sent>0 THEN 'partial' ELSE 'reserved' END) THEN RAISE EXCEPTION 'Preparation state mismatch'; END IF;
   ELSIF NEW.kind='cancel' THEN
    IF recordrow.status<>'cancelled' OR EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.context_id=NEW.context_id AND nkt_current_state(p.id)='reserved' AND nkt_current_preparation(p.id)=NEW.preparation_id) THEN RAISE EXCEPTION 'Cancellation retained reservations'; END IF;
   ELSIF NEW.kind='restore' AND recordrow.status NOT IN ('reconciliation','cancelled') THEN RAISE EXCEPTION 'Compensated preparation requires review'; END IF;
  END IF;

 ELSE
  IF NOT EXISTS(SELECT 1 FROM nkt_documents WHERE context_id=NEW.context_id AND (CASE WHEN TG_TABLE_NAME='nkt_plan_revisions' THEN revision_id=NEW.id ELSE correction_id=NEW.id END)) THEN RAISE EXCEPTION 'Immutable review PDF required'; END IF;
 END IF;RETURN NULL;
END $$;
CREATE TRIGGER nkt_preparation_guard BEFORE INSERT OR UPDATE OR DELETE ON nkt_preparations FOR EACH ROW EXECUTE FUNCTION nkt_loading_guard();
CREATE TRIGGER nkt_plan_revisions_guard BEFORE INSERT ON nkt_plan_revisions FOR EACH ROW EXECUTE FUNCTION nkt_loading_guard();
CREATE TRIGGER nkt_plan_revisions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_plan_revisions FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE CONSTRAINT TRIGGER nkt_plan_revisions_final AFTER INSERT ON nkt_plan_revisions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_loading_final();
CREATE TRIGGER nkt_operations_guard BEFORE INSERT ON nkt_operations FOR EACH ROW EXECUTE FUNCTION nkt_loading_guard();
CREATE TRIGGER nkt_operations_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_operations FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE CONSTRAINT TRIGGER nkt_operations_final AFTER INSERT ON nkt_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_loading_final();
CREATE TRIGGER nkt_corrections_guard BEFORE INSERT ON nkt_corrections FOR EACH ROW EXECUTE FUNCTION nkt_loading_guard();
CREATE TRIGGER nkt_corrections_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_corrections FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE CONSTRAINT TRIGGER nkt_corrections_final AFTER INSERT ON nkt_corrections DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_loading_final();
CREATE TRIGGER nkt_stock_events_guard BEFORE INSERT ON nkt_stock_events FOR EACH ROW EXECUTE FUNCTION nkt_loading_guard();
CREATE TRIGGER nkt_stock_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_stock_events FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_documents_guard BEFORE INSERT ON nkt_documents FOR EACH ROW EXECUTE FUNCTION nkt_loading_guard();
CREATE TRIGGER nkt_documents_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_documents FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
