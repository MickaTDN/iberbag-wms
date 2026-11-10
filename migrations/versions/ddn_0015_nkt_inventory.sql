CREATE FUNCTION nkt_inventory_resolve(code_value text,full_value boolean,scope_id integer) RETURNS integer LANGUAGE sql STABLE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT CASE WHEN count(*)=1 THEN min(p.id) END FROM nkt_pallets p WHERE p.context_id=scope_id AND (CASE WHEN length(code_value)=4 AND NOT full_value THEN lower(right(p.peg,4))=lower(code_value) ELSE lower(p.peg)=lower(code_value) END)
$$;
CREATE FUNCTION nkt_inventory_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE inv record;rev record;op record;previous record;original record;g jsonb;node jsonb;cnt integer;used bigint;cap integer;loc text;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF TG_TABLE_NAME='nkt_operations' THEN
  IF NEW.kind NOT IN ('inventory_missing','inventory_restore') THEN RETURN NEW; END IF;
 END IF;
 IF TG_TABLE_NAME='nkt_stock_events' THEN
  SELECT * INTO op FROM nkt_operations WHERE id=NEW.operation_id AND context_id=NEW.context_id;
  IF op.kind NOT IN ('inventory_missing','inventory_restore') THEN RETURN NEW; END IF;
  SELECT * INTO previous FROM nkt_stock_events WHERE context_id=NEW.context_id AND pallet_id=NEW.pallet_id ORDER BY id DESC LIMIT 1;
  IF NEW.previous_event_id IS DISTINCT FROM previous.id OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'events') e WHERE (e->>'pallet_id')::integer=NEW.pallet_id AND (e->>'previous_event_id')::integer IS NOT DISTINCT FROM NEW.previous_event_id AND e->>'state'=NEW.state) THEN RAISE EXCEPTION 'Stale/unreviewed inventory event'; END IF;
  IF op.kind='inventory_missing' THEN
   IF coalesce(previous.state,'available')<>'available' OR NEW.state<>'missing' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'report'->'groups') row,LATERAL jsonb_array_elements(row->'source'->'pallets') p WHERE row->>'decision'='not_located' AND row->'source'->>'excluded' IS NULL AND (p->>'id')::integer=NEW.pallet_id AND p->>'state'='available') THEN RAISE EXCEPTION 'Only eligible available PEG can be inventory missing'; END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op.snapshot::jsonb->'report'->'effects') e WHERE (e->'pallet'->>'id')::integer=NEW.pallet_id AND (e->'pallet'->>'event')::integer=NEW.previous_event_id) THEN RAISE EXCEPTION 'PEG outside restoration effects'; END IF;
   IF previous.state IS DISTINCT FROM 'missing' OR previous.operation_id IS DISTINCT FROM op.corrects_id OR NEW.state<>'available' THEN RAISE EXCEPTION 'Unsafe/duplicated inventory restoration'; END IF;
   IF EXISTS(SELECT 1 FROM nkt_pallets p JOIN nkt_pallets target ON target.entry_id=p.entry_id AND target.context_id=p.context_id WHERE target.id=NEW.pallet_id AND p.context_id=NEW.context_id AND nkt_current_state(p.id)='reserved') THEN RAISE EXCEPTION 'Reserved inventory load requires reconciliation'; END IF;
   SELECT p.local,l.capacidade INTO loc,cap FROM nkt_pallets p JOIN locations l ON l.context_id=p.context_id AND l.codigo=p.local AND l.active WHERE p.context_id=NEW.context_id AND p.id=NEW.pallet_id;
   IF NOT FOUND THEN RAISE EXCEPTION 'Original inventory location unavailable'; END IF;
   SELECT count(*) INTO used FROM nkt_pallets p WHERE p.context_id=NEW.context_id AND p.local=loc AND nkt_current_state(p.id) NOT IN ('shipped','missing');
   used=used+(SELECT coalesce(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=loc)+(SELECT coalesce(sum(physical_quantity),0) FROM valmet_positions WHERE context_id=NEW.context_id AND local=loc);
   IF cap IS NOT NULL AND used+1>cap THEN RAISE EXCEPTION 'Inventory restoration exceeds capacity'; END IF;
   IF EXISTS(SELECT 1 FROM physical_location_claims f JOIN storage_contexts c ON c.warehouse_id=f.warehouse_id WHERE c.id=NEW.context_id AND f.code=lower(btrim(loc)) AND f.context_id<>NEW.context_id) THEN RAISE EXCEPTION 'Inventory location belongs to another context'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='nkt_inventories' THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Inventory history retained'; END IF;
  IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN nkt_owners o ON o.client_id=c.client_id JOIN clients a ON a.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND a.active AND w.active AND w.name='Escapães') THEN RAISE EXCEPTION 'NKT Escapães required'; END IF;
  IF TG_OP='INSERT' THEN
   IF (SELECT count(DISTINCT x::integer) FROM jsonb_array_elements_text(NEW.scope::jsonb) x)<>jsonb_array_length(NEW.scope::jsonb) OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.scope::jsonb) x WHERE NOT EXISTS(SELECT 1 FROM nkt_entries WHERE id=x::integer AND context_id=NEW.context_id AND status='confirmed')) THEN RAISE EXCEPTION 'Invalid inventory scope entries'; END IF;
   IF NEW.version<>1 OR NEW.status<>'draft' OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role IN ('admin','operador')) OR jsonb_typeof(NEW.scope::jsonb) IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.scope::jsonb) NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'Inventory draft/scope required'; END IF;
  ELSE
   IF OLD.status='confirmed' OR ROW(NEW.context_id,NEW.created_by,NEW.created_at,NEW.title,NEW.mode,NEW.scope,NEW.opening,NEW.request_key) IS DISTINCT FROM ROW(OLD.context_id,OLD.created_by,OLD.created_at,OLD.title,OLD.mode,OLD.scope,OLD.opening,OLD.request_key) OR NEW.version NOT BETWEEN OLD.version AND OLD.version+1 THEN RAISE EXCEPTION 'Immutable inventory scope/confirmed header'; END IF;
   IF NEW.status='confirmed' AND NOT EXISTS(SELECT 1 FROM nkt_operations WHERE inventory_id=NEW.id AND context_id=NEW.context_id AND kind='inventory_missing') THEN RAISE EXCEPTION 'Inventory adjustment operation required'; END IF;
  END IF;RETURN NEW;
 END IF;
 SELECT * INTO inv FROM nkt_inventories WHERE id=NEW.inventory_id AND context_id=NEW.context_id;
 IF inv.id IS NULL OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador') AND (role='admin' OR id=inv.created_by)) OR NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN nkt_owners o ON o.client_id=c.client_id JOIN clients a ON a.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND a.active AND w.active AND w.name='Escapães') THEN RAISE EXCEPTION 'Inventory context/actor required'; END IF;
 IF TG_TABLE_NAME='nkt_inventory_checks' THEN
  IF inv.status<>'draft' OR coalesce(btrim(NEW.reason),'')='' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(inv.scope::jsonb) x WHERE x::integer=NEW.entry_id) OR (NEW.snapshot::jsonb->>'entry_id')::integer IS DISTINCT FROM NEW.entry_id THEN RAISE EXCEPTION 'Inventory check outside scope'; END IF;
  SELECT count(*) INTO cnt FROM nkt_pallets p WHERE p.entry_id=NEW.entry_id AND p.context_id=NEW.context_id;
  IF (SELECT count(DISTINCT (x->>'id')::integer) FROM jsonb_array_elements(NEW.snapshot::jsonb->'pallets') x)<>cnt THEN RAISE EXCEPTION 'Duplicate/missing source PEG'; END IF;
  IF cnt<>jsonb_array_length(NEW.snapshot::jsonb->'pallets') OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.snapshot::jsonb->'pallets') x LEFT JOIN nkt_pallets p ON p.id=(x->>'id')::integer AND p.entry_id=NEW.entry_id AND p.context_id=NEW.context_id WHERE p.id IS NULL OR p.peg IS DISTINCT FROM x->>'peg' OR nkt_current_state(p.id) IS DISTINCT FROM x->>'state' OR (x->>'event')::integer IS DISTINCT FROM (SELECT max(id) FROM nkt_stock_events WHERE pallet_id=p.id AND context_id=NEW.context_id)) THEN RAISE EXCEPTION 'Stale inventory check'; END IF;
  IF NEW.state='excluded' THEN
   IF NEW.snapshot::jsonb->>'excluded' IS NULL OR NOT ((NEW.snapshot::jsonb->>'excluded'='reserved' AND EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.entry_id=NEW.entry_id AND p.context_id=NEW.context_id AND nkt_current_state(p.id)='reserved')) OR (NEW.snapshot::jsonb->>'excluded'='no_physical' AND NOT EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.entry_id=NEW.entry_id AND p.context_id=NEW.context_id AND nkt_current_state(p.id) IN ('available','reserved'))) OR (NEW.snapshot::jsonb->>'excluded'='legacy_unmapped' AND NOT EXISTS(SELECT 1 FROM nkt_containers WHERE entry_id=NEW.entry_id AND context_id=NEW.context_id))) THEN RAISE EXCEPTION 'Cannot arbitrarily exclude eligible load'; END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM nkt_containers WHERE entry_id=NEW.entry_id AND context_id=NEW.context_id) OR NEW.snapshot::jsonb->>'excluded' IS NOT NULL OR EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.entry_id=NEW.entry_id AND p.context_id=NEW.context_id AND nkt_current_state(p.id)='reserved') THEN RAISE EXCEPTION 'Reserved/unknown load cannot be missing'; END IF;
   IF NEW.state='located' THEN
    IF NEW.pallet_id IS NULL OR NOT EXISTS(SELECT 1 FROM nkt_pallets WHERE id=NEW.pallet_id AND entry_id=NEW.entry_id AND context_id=NEW.context_id AND nkt_current_state(id) IN ('available','reserved')) THEN RAISE EXCEPTION 'Physical communicated PEG required'; END IF;
    SELECT count(*) INTO cnt FROM nkt_pallets WHERE context_id=NEW.context_id AND (CASE WHEN NOT NEW.full_peg AND length(NEW.communicated)=4 THEN lower(right(peg,4))=lower(NEW.communicated) ELSE lower(peg)=lower(NEW.communicated) END);
    IF cnt<>1 OR NOT EXISTS(SELECT 1 FROM nkt_pallets WHERE id=NEW.pallet_id AND (CASE WHEN NOT NEW.full_peg AND length(NEW.communicated)=4 THEN lower(right(peg,4))=lower(NEW.communicated) ELSE lower(peg)=lower(NEW.communicated) END)) THEN RAISE EXCEPTION 'Ambiguous/unresolved communicated PEG'; END IF;
   END IF;
  END IF;
 ELSIF TG_TABLE_NAME='nkt_inventory_incidents' THEN
  IF NEW.resolves_id IS NOT NULL AND (NEW.kind<>'resolved' OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND role='admin' AND active) OR NOT EXISTS(SELECT 1 FROM nkt_inventory_incidents WHERE id=NEW.resolves_id AND inventory_id=inv.id AND context_id=NEW.context_id AND resolves_id IS NULL)) THEN RAISE EXCEPTION 'Administrative incident decision required'; END IF;
 ELSIF TG_TABLE_NAME='nkt_inventory_reviews' THEN
  IF NEW.version<>inv.version OR NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') OR coalesce(btrim(NEW.reason),'')='' THEN RAISE EXCEPTION 'Invalid inventory review'; END IF;
  IF NEW.kind IN ('confirm','restore') AND NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND role='admin' AND active) THEN RAISE EXCEPTION 'Administrative inventory approval required'; END IF;
  IF NEW.kind='confirm' AND (jsonb_typeof(NEW.payload::jsonb->'groups') IS DISTINCT FROM 'array' OR jsonb_typeof(NEW.payload::jsonb->'blocking') IS DISTINCT FROM 'array' OR jsonb_typeof(NEW.payload::jsonb->'scope') IS DISTINCT FROM 'array') THEN RAISE EXCEPTION 'Explicit inventory review scope/groups required'; END IF;
  IF NEW.kind='confirm' AND (inv.status<>'draft' OR NEW.payload::jsonb->'scope' IS DISTINCT FROM inv.scope::jsonb OR jsonb_array_length(NEW.payload::jsonb->'blocking')<>0) THEN RAISE EXCEPTION 'Incomplete inventory review'; END IF;
  IF NEW.kind='restore' AND (jsonb_typeof(NEW.payload::jsonb->'effects') IS DISTINCT FROM 'array' OR coalesce(jsonb_array_length(NEW.payload::jsonb->'effects'),0) NOT BETWEEN 1 AND 60 OR NEW.payload::jsonb->>'resolved' IS NULL) THEN RAISE EXCEPTION 'Explicit restoration identity/effects required'; END IF;
  IF NEW.kind='restore' AND (inv.status<>'confirmed' OR NOT EXISTS(SELECT 1 FROM nkt_operations WHERE id=NEW.original_id AND inventory_id=inv.id AND context_id=NEW.context_id AND kind='inventory_missing')) THEN RAISE EXCEPTION 'Original inventory shortage required'; END IF;
  IF NEW.kind='restore' AND (NEW.payload::jsonb->>'resolved')::integer IS DISTINCT FROM nkt_inventory_resolve(NEW.payload::jsonb->>'communicated',coalesce((NEW.payload::jsonb->>'full_peg')::boolean,false),NEW.context_id) THEN RAISE EXCEPTION 'Ambiguous restoration identifier'; END IF;
 ELSE
  SELECT * INTO rev FROM nkt_inventory_reviews WHERE id=NEW.inventory_review_id AND inventory_id=inv.id AND context_id=NEW.context_id;
  IF rev.id IS NULL OR NOT EXISTS(SELECT 1 FROM users WHERE username=NEW.actor AND role='admin' AND active) OR (NEW.snapshot::jsonb->'report') IS DISTINCT FROM rev.payload::jsonb OR jsonb_typeof(NEW.snapshot::jsonb->'events') IS DISTINCT FROM 'array' OR (NEW.snapshot::jsonb->>'inventory_id')::integer IS DISTINCT FROM inv.id THEN RAISE EXCEPTION 'Unreviewed inventory operation'; END IF;
  IF NEW.kind='inventory_missing' THEN
   IF inv.status<>'draft' OR rev.kind<>'confirm' OR rev.version<>inv.version OR jsonb_array_length(rev.payload::jsonb->'blocking')<>0 OR rev.payload::jsonb->'scope' IS DISTINCT FROM inv.scope::jsonb OR jsonb_array_length(rev.payload::jsonb->'groups')<>jsonb_array_length(inv.scope::jsonb) THEN RAISE EXCEPTION 'Inventory scope/incomplete version'; END IF;
   IF (SELECT count(DISTINCT (sg.value->'source'->>'entry_id')::integer) FROM jsonb_array_elements(rev.payload::jsonb->'groups') AS sg(value))<>jsonb_array_length(inv.scope::jsonb) OR EXISTS(SELECT 1 FROM jsonb_array_elements(rev.payload::jsonb->'groups') AS sg(value) WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(inv.scope::jsonb) x WHERE x::integer=(sg.value->'source'->>'entry_id')::integer)) THEN RAISE EXCEPTION 'All inventory scope loads must be represented exactly once'; END IF;
   IF inv.mode='complete' AND EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.context_id=NEW.context_id AND nkt_current_state(p.id) IN ('available','reserved') AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(inv.scope::jsonb) x WHERE x::integer=p.entry_id)) THEN RAISE EXCEPTION 'New complete-inventory load requires conference'; END IF;
   FOR g IN SELECT value FROM jsonb_array_elements(rev.payload::jsonb->'groups') LOOP
    IF NOT EXISTS(SELECT 1 FROM nkt_inventory_checks WHERE id=(g->>'check_id')::integer AND inventory_id=inv.id AND context_id=NEW.context_id AND entry_id=(g->'source'->>'entry_id')::integer AND state=g->>'decision' AND snapshot::jsonb=g->'source' AND id=(SELECT max(c.id) FROM nkt_inventory_checks c WHERE c.inventory_id=inv.id AND c.context_id=NEW.context_id AND c.entry_id=(g->'source'->>'entry_id')::integer)) THEN RAISE EXCEPTION 'Missing/latest inventory check required'; END IF;
    IF g->>'decision'='unverified' OR g->'source'->>'excluded'='legacy_unmapped' THEN RAISE EXCEPTION 'Unverified legacy load cannot be absent'; END IF;
    FOR node IN SELECT value FROM jsonb_array_elements(g->'source'->'pallets') LOOP
     IF nkt_current_state((node->>'id')::integer) IS DISTINCT FROM node->>'state' OR (node->>'event')::integer IS DISTINCT FROM (SELECT max(id) FROM nkt_stock_events WHERE pallet_id=(node->>'id')::integer AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Movements after inventory conference'; END IF;
    END LOOP;
   END LOOP;
   IF EXISTS(SELECT 1 FROM nkt_inventory_incidents i WHERE i.inventory_id=inv.id AND i.context_id=NEW.context_id AND i.resolves_id IS NULL AND NOT EXISTS(SELECT 1 FROM nkt_inventory_incidents r WHERE r.resolves_id=i.id AND r.context_id=NEW.context_id)) THEN RAISE EXCEPTION 'Pending inventory occurrence'; END IF;
  ELSE
   IF inv.status<>'confirmed' OR rev.kind<>'restore' OR NEW.corrects_id IS DISTINCT FROM rev.original_id THEN RAISE EXCEPTION 'Invalid inventory compensation'; END IF;
   IF (rev.payload::jsonb->>'resolved')::integer IS DISTINCT FROM nkt_inventory_resolve(rev.payload::jsonb->>'communicated',coalesce((rev.payload::jsonb->>'full_peg')::boolean,false),NEW.context_id) THEN RAISE EXCEPTION 'Identifier became ambiguous before restoration'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION nkt_inventory_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE rev record;expected integer;
BEGIN
 IF TG_TABLE_NAME='nkt_operations' THEN
  IF NEW.kind NOT IN ('inventory_missing','inventory_restore') THEN RETURN NULL; END IF;
  SELECT * INTO rev FROM nkt_inventory_reviews WHERE id=NEW.inventory_review_id AND context_id=NEW.context_id;
  IF NEW.kind='inventory_missing' THEN
   SELECT count(*) INTO expected FROM jsonb_array_elements(rev.payload::jsonb->'groups') g,LATERAL jsonb_array_elements(g->'source'->'pallets') p WHERE g->>'decision'='not_located' AND g->'source'->>'excluded' IS NULL AND p->>'state'='available';
   IF NOT EXISTS(SELECT 1 FROM nkt_inventories WHERE id=NEW.inventory_id AND context_id=NEW.context_id AND status='confirmed') THEN RAISE EXCEPTION 'Inventory must confirm atomically'; END IF;
  ELSE expected=jsonb_array_length(rev.payload::jsonb->'effects'); END IF;
  IF expected<>(SELECT count(*) FROM nkt_stock_events WHERE operation_id=NEW.id AND context_id=NEW.context_id) OR NOT EXISTS(SELECT 1 FROM nkt_documents WHERE operation_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Incomplete inventory adjustment/document'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM nkt_documents WHERE inventory_review_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Inventory PDF required'; END IF;
 END IF;RETURN NULL;
END $$;
CREATE TRIGGER nkt_inventories_guard BEFORE INSERT OR UPDATE OR DELETE ON nkt_inventories FOR EACH ROW EXECUTE FUNCTION nkt_inventory_guard();
CREATE TRIGGER aa_nkt_inventory_operation BEFORE INSERT ON nkt_operations FOR EACH ROW EXECUTE FUNCTION nkt_inventory_guard();
CREATE TRIGGER aa_nkt_inventory_event BEFORE INSERT ON nkt_stock_events FOR EACH ROW EXECUTE FUNCTION nkt_inventory_guard();
CREATE CONSTRAINT TRIGGER nkt_inventory_operation_final AFTER INSERT ON nkt_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_inventory_final();
CREATE TRIGGER nkt_inventory_checks_guard BEFORE INSERT ON nkt_inventory_checks FOR EACH ROW EXECUTE FUNCTION nkt_inventory_guard();
CREATE TRIGGER nkt_inventory_checks_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_inventory_checks FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_inventory_reviews_guard BEFORE INSERT ON nkt_inventory_reviews FOR EACH ROW EXECUTE FUNCTION nkt_inventory_guard();
CREATE TRIGGER nkt_inventory_reviews_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_inventory_reviews FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_inventory_incidents_guard BEFORE INSERT ON nkt_inventory_incidents FOR EACH ROW EXECUTE FUNCTION nkt_inventory_guard();
CREATE TRIGGER nkt_inventory_incidents_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_inventory_incidents FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE CONSTRAINT TRIGGER nkt_inventory_review_final AFTER INSERT ON nkt_inventory_reviews DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_inventory_final();
