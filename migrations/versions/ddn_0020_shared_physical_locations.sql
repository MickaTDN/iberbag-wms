CREATE FUNCTION ddn_shared_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.status<>'draft' OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role='admin') OR length(btrim(NEW.reason))=0 THEN
      RAISE EXCEPTION 'Administrative physical review required' USING ERRCODE='23514'; END IF;
  ELSE
    IF TG_OP='DELETE' OR OLD.status<>'draft' OR NEW.status<>'confirmed' OR NEW.confirmed_at IS NULL
      OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.confirmed_by AND active AND role='admin')
      OR (to_jsonb(NEW)-ARRAY['status','confirmed_by','confirmed_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','confirmed_by','confirmed_at']) THEN
      RAISE EXCEPTION 'Physical review immutable' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER physical_review_guard BEFORE INSERT OR UPDATE OR DELETE ON physical_sharing_reviews FOR EACH ROW EXECUTE FUNCTION ddn_shared_review_guard();

CREATE FUNCTION ddn_shared_catalogue_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r physical_sharing_reviews; master physical_locations; ctx integer;
BEGIN
  PERFORM pg_advisory_xact_lock(72834001);
  IF TG_TABLE_NAME='physical_locations' THEN
    SELECT * INTO r FROM physical_sharing_reviews WHERE id=NEW.review_id;
    IF r.status IS DISTINCT FROM 'confirmed' OR NEW.warehouse_id<>1 OR NEW.code<>r.code OR NEW.label<>r.label OR NEW.code<>lower(btrim(NEW.code)) OR NEW.code IN ('','chão','chao','(não indicado)') THEN
      RAISE EXCEPTION 'Confirmed matching physical review required' USING ERRCODE='23514'; END IF;
    IF (SELECT count(*) FROM storage_contexts c JOIN clients u ON u.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id
        WHERE c.id IN (r.source_context_id,r.other_context_id) AND c.client_id IN (1,3) AND c.warehouse_id=1 AND c.active AND u.active AND w.active)<>2
      OR (SELECT count(DISTINCT client_id) FROM storage_contexts WHERE id IN(r.source_context_id,r.other_context_id))<>2
      OR NOT EXISTS(SELECT 1 FROM physical_location_claims WHERE warehouse_id=1 AND code=NEW.code AND context_id IN(r.source_context_id,r.other_context_id)) THEN
      RAISE EXCEPTION 'Sharing only Iberbag Cosmopak Ovar' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM locations l JOIN storage_contexts c ON c.id=l.context_id WHERE c.warehouse_id=1 AND lower(btrim(l.codigo))=NEW.code
       AND (l.context_id NOT IN(r.source_context_id,r.other_context_id) OR l.codigo<>NEW.label OR l.capacidade IS DISTINCT FROM 1 OR NOT l.active))
      OR (SELECT count(*) FROM locations WHERE context_id=r.source_context_id AND codigo=NEW.label)<>1
      OR EXISTS(SELECT 1 FROM stock s JOIN storage_contexts c ON c.id=s.context_id WHERE c.warehouse_id=1 AND lower(btrim(s.local))=NEW.code
          AND (s.local<>NEW.label OR s.context_id NOT IN(r.source_context_id,r.other_context_id) OR s.quantidade>1 OR s.stacked))
      OR (SELECT coalesce(sum(s.quantidade),0) FROM stock s JOIN storage_contexts c ON c.id=s.context_id WHERE c.warehouse_id=1 AND lower(btrim(s.local))=NEW.code)>1 THEN
      RAISE EXCEPTION 'Reconcile catalogue capacity identities and physical stock first' USING ERRCODE='23514'; END IF;
    IF EXISTS(SELECT 1 FROM dispatch_picks p JOIN dispatch_lines l ON l.id=p.line_id JOIN dispatch_orders o ON o.id=l.order_id JOIN storage_contexts c ON c.id=o.context_id
      WHERE c.warehouse_id=1 AND lower(btrim(p.local))=NEW.code AND (o.status IN('ready','checked','blocked') OR (o.status='confirmed' AND o.office
        AND NOT EXISTS(SELECT 1 FROM preparation_completions x WHERE x.order_id=o.id) AND NOT EXISTS(SELECT 1 FROM preparation_compensations x WHERE x.order_id=o.id))))
      OR EXISTS(SELECT 1 FROM putaways p JOIN storage_contexts c ON c.id=p.context_id WHERE c.warehouse_id=1 AND lower(btrim(p.local))=NEW.code AND p.status='draft')
      OR EXISTS(SELECT 1 FROM stock_transfers t JOIN storage_contexts c ON c.id=t.context_id WHERE c.warehouse_id=1 AND (lower(btrim(t.destination_local))=NEW.code OR lower(btrim(t.source_local))=NEW.code) AND t.status='draft')
      OR EXISTS(SELECT 1 FROM stock_incidents i LEFT JOIN dispatch_picks k ON k.id=i.pick_id LEFT JOIN inventory_scopes sc ON sc.id=i.scope_id JOIN storage_contexts c ON c.id=i.context_id
        WHERE c.warehouse_id=1 AND i.status='open' AND (lower(btrim(k.local))=NEW.code OR lower(btrim(sc.local))=NEW.code)) THEN
      RAISE EXCEPTION 'Reconcile pending physical movements before sharing' USING ERRCODE='23514'; END IF;
  ELSE
    SELECT * INTO master FROM physical_locations WHERE id=NEW.physical_id;
    SELECT * INTO r FROM physical_sharing_reviews WHERE id=master.review_id;
    IF NEW.context_id NOT IN(r.source_context_id,r.other_context_id) THEN RAISE EXCEPTION 'Unapproved physical access' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER physical_catalogue_guard BEFORE INSERT ON physical_locations FOR EACH ROW EXECUTE FUNCTION ddn_shared_catalogue_guard();
CREATE TRIGGER physical_access_guard BEFORE INSERT ON physical_location_access FOR EACH ROW EXECUTE FUNCTION ddn_shared_catalogue_guard();
CREATE FUNCTION ddn_shared_initial_state() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 IF TG_RELID<>'physical_locations'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted physical catalogue trigger' USING ERRCODE='23514';END IF;
 INSERT INTO physical_location_state(physical_id,state,version) VALUES(NEW.id,'free',0);RETURN NEW; END $$;
CREATE TRIGGER physical_initial_state AFTER INSERT ON physical_locations FOR EACH ROW EXECUTE FUNCTION ddn_shared_initial_state();
CREATE FUNCTION ddn_shared_activation_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT count(*) FROM physical_location_access WHERE physical_id=NEW.id)<>2
    OR (SELECT count(*) FROM locations l JOIN physical_location_access a ON a.context_id=l.context_id AND a.physical_id=NEW.id WHERE l.codigo=NEW.label AND l.capacidade=1 AND l.active)<>2
    OR NOT EXISTS(SELECT 1 FROM physical_location_events WHERE physical_id=NEW.id AND kind='activate' AND ref_id=NEW.review_id) THEN
    RAISE EXCEPTION 'Incomplete physical sharing activation' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER physical_activation_complete AFTER INSERT ON physical_locations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ddn_shared_activation_complete();

CREATE OR REPLACE FUNCTION ddn_claim_position() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE physical_code text; wh integer; owner integer; p physical_locations;
BEGIN
  PERFORM pg_advisory_xact_lock(72834001);
  IF TG_TABLE_NAME='locations' THEN physical_code=lower(btrim(NEW.codigo));ELSE physical_code=lower(btrim(NEW.local));END IF;
  IF physical_code IN ('','chão','chao','(não indicado)') THEN RETURN NEW;END IF;
  SELECT warehouse_id INTO wh FROM storage_contexts WHERE id=NEW.context_id;
  SELECT * INTO p FROM physical_locations WHERE warehouse_id=wh AND code=physical_code;
  IF FOUND THEN
    IF NOT EXISTS(SELECT 1 FROM physical_location_access WHERE physical_id=p.id AND context_id=NEW.context_id) THEN
      RAISE EXCEPTION 'Physical position access denied' USING ERRCODE='23514';END IF;
    IF TG_TABLE_NAME='locations' THEN
      IF NEW.codigo<>p.label OR NEW.capacidade IS DISTINCT FROM 1 OR NOT NEW.active THEN RAISE EXCEPTION 'Shared physical catalogue is common and capacity one' USING ERRCODE='23514';END IF;
    ELSE
      IF NEW.local<>p.label THEN RAISE EXCEPTION 'Physical label mismatch' USING ERRCODE='23514';END IF;
    END IF;
    RETURN NEW;
  END IF;
  INSERT INTO physical_location_claims(warehouse_id,code,context_id) VALUES(wh,physical_code,NEW.context_id) ON CONFLICT(warehouse_id,code) DO NOTHING;
  SELECT context_id INTO owner FROM physical_location_claims WHERE warehouse_id=wh AND code=physical_code;
  IF owner IS DISTINCT FROM NEW.context_id THEN RAISE EXCEPTION 'Physical position belongs to another context' USING ERRCODE='23514';END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION ddn_shared_cancel_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME='putaways' THEN
    IF TG_OP='UPDATE' AND EXISTS(SELECT 1 FROM physical_locations p JOIN storage_contexts c ON c.warehouse_id=p.warehouse_id WHERE c.id=OLD.context_id AND p.code=lower(btrim(OLD.local))) THEN
      IF (NEW.context_id,NEW.line_id,NEW.local,NEW.quantidade,NEW.created_by,NEW.request_key,NEW.stacked) IS DISTINCT FROM (OLD.context_id,OLD.line_id,OLD.local,OLD.quantidade,OLD.created_by,OLD.request_key,OLD.stacked) THEN
        RAISE EXCEPTION 'Pending physical assignment identity immutable' USING ERRCODE='23514';END IF;
    END IF;
    IF OLD.status='cancelled' THEN RAISE EXCEPTION 'Cancelled putaway immutable' USING ERRCODE='23514';END IF;
    IF TG_OP='DELETE' THEN RETURN OLD;END IF;
  ELSE
    IF EXISTS(SELECT 1 FROM physical_locations p JOIN storage_contexts c ON c.warehouse_id=p.warehouse_id WHERE c.id=OLD.context_id AND lower(btrim(OLD.codigo))=p.code) THEN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Shared physical binding cannot be removed' USING ERRCODE='23514';END IF;
      IF (NEW.codigo,NEW.context_id,NEW.capacidade,NEW.active) IS DISTINCT FROM (OLD.codigo,OLD.context_id,1,true) THEN
        RAISE EXCEPTION 'Manage common physical catalogue, not owner copies' USING ERRCODE='23514';END IF;
    END IF;
    IF TG_OP='DELETE' THEN RETURN OLD;END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER physical_cancel_guard BEFORE UPDATE OR DELETE ON putaways FOR EACH ROW EXECUTE FUNCTION ddn_shared_cancel_guard();
CREATE TRIGGER physical_binding_guard BEFORE UPDATE OR DELETE ON locations FOR EACH ROW EXECUTE FUNCTION ddn_shared_cancel_guard();

CREATE FUNCTION ddn_shared_stock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p physical_locations; h physical_location_state;
BEGIN
  PERFORM pg_advisory_xact_lock(72834001);
  IF TG_OP='UPDATE' THEN
    IF (NEW.context_id,NEW.artigo,NEW.lote,NEW.carga,NEW.local,NEW.stacked) IS DISTINCT FROM (OLD.context_id,OLD.artigo,OLD.lote,OLD.carga,OLD.local,OLD.stacked)
      AND EXISTS(SELECT 1 FROM physical_locations p0 JOIN storage_contexts c ON c.warehouse_id=p0.warehouse_id WHERE c.id IN(OLD.context_id,NEW.context_id) AND p0.code IN(lower(btrim(OLD.local)),lower(btrim(NEW.local)))) THEN
      RAISE EXCEPTION 'Shared physical stock identity immutable' USING ERRCODE='23514';END IF;
  END IF;
  SELECT p0.* INTO p FROM physical_locations p0 JOIN storage_contexts c ON c.warehouse_id=p0.warehouse_id WHERE c.id=NEW.context_id AND p0.code=lower(btrim(NEW.local));
  IF NOT FOUND THEN RETURN NEW;END IF;
  SELECT * INTO h FROM physical_location_state WHERE physical_id=p.id; -- protected by the same transaction advisory lock; runtime remains read-only
  IF NEW.stacked OR NEW.quantidade>1 OR NOT EXISTS(SELECT 1 FROM physical_location_access WHERE physical_id=p.id AND context_id=NEW.context_id) THEN
    RAISE EXCEPTION 'Shared rack requires one normal physical pallet' USING ERRCODE='23514';END IF;
  IF NEW.quantidade>0 AND (h.context_id IS DISTINCT FROM NEW.context_id OR h.state='free'
     OR (h.state IN('occupied','awaiting_collection') AND h.stock_id IS DISTINCT FROM NEW.id)) THEN
    RAISE EXCEPTION 'Physical position occupied or no pending assignment' USING ERRCODE='23514';END IF;
  IF NEW.quantidade>0 THEN
    IF h.state='awaiting_collection' THEN RAISE EXCEPTION 'Accounting dispatch requires physical reconciliation before restock' USING ERRCODE='23514';END IF;
    IF h.state='pending_putaway' AND NOT EXISTS(SELECT 1 FROM putaways x JOIN receipt_lines l ON l.id=x.line_id AND l.context_id=x.context_id JOIN receipts r ON r.id=l.receipt_id AND r.context_id=l.context_id
      WHERE x.id=h.source_id AND x.context_id=NEW.context_id AND x.status='draft' AND r.status='confirmed' AND l.artigo=NEW.artigo AND l.lote=NEW.lote AND r.code=NEW.carga) THEN
      RAISE EXCEPTION 'Physical putaway content mismatch' USING ERRCODE='23514';END IF;
    IF h.state='pending_transfer' AND NOT EXISTS(SELECT 1 FROM stock_transfers x WHERE x.id=h.source_id AND x.context_id=NEW.context_id AND x.status='draft' AND x.artigo=NEW.artigo AND x.lote=NEW.lote AND x.carga=NEW.carga) THEN
      RAISE EXCEPTION 'Physical transfer content mismatch' USING ERRCODE='23514';END IF;
    IF h.state='pending_inventory' AND NOT EXISTS(SELECT 1 FROM inventory_counts x JOIN inventory_scopes sc ON sc.id=x.scope_id AND sc.context_id=x.context_id
      WHERE sc.inventory_id=h.source_id AND sc.local=p.label AND x.context_id=NEW.context_id AND NOT x.pending AND x.artigo=NEW.artigo AND x.lote=NEW.lote AND x.carga=NEW.carga AND x.quantidade=NEW.quantidade AND NOT x.stacked) THEN
      RAISE EXCEPTION 'Physical inventory content mismatch' USING ERRCODE='23514';END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER shared_stock_guard BEFORE INSERT OR UPDATE ON stock FOR EACH ROW EXECUTE FUNCTION ddn_shared_stock_guard();
CREATE FUNCTION ddn_shared_stock_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p physical_locations;
BEGIN
  SELECT p0.* INTO p FROM physical_locations p0 JOIN storage_contexts c ON c.warehouse_id=p0.warehouse_id WHERE c.id=NEW.context_id AND p0.code=lower(btrim(NEW.local));
  IF FOUND AND (SELECT coalesce(sum(s.quantidade),0) FROM stock s JOIN storage_contexts c ON c.id=s.context_id WHERE c.warehouse_id=p.warehouse_id AND lower(btrim(s.local))=p.code)>1 THEN
    RAISE EXCEPTION 'Shared physical capacity exceeded' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER shared_stock_capacity AFTER INSERT OR UPDATE ON stock DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ddn_shared_stock_capacity();

CREATE FUNCTION ddn_shared_event() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE p physical_locations; h physical_location_state; n physical_location_state;
  u users; put putaways; tr stock_transfers; inv physical_inventories; ord dispatch_orders;
  stk stock; oldstock stock; r physical_sharing_reviews; scope inventory_scopes; total integer;
BEGIN
  IF TG_RELID<>'physical_location_events'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted physical event trigger' USING ERRCODE='23514';END IF;
  PERFORM pg_advisory_xact_lock(72834001);
  SELECT * INTO u FROM users WHERE id=NEW.actor_id AND active AND role IN('admin','operador');
  SELECT * INTO p FROM physical_locations WHERE id=NEW.physical_id;
  IF u.id IS NULL OR p.id IS NULL OR length(btrim(NEW.reason)) NOT BETWEEN 1 AND 2000 OR NEW.ref_id<1
    OR NOT EXISTS(SELECT 1 FROM physical_location_access WHERE physical_id=p.id AND context_id=NEW.context_id) THEN
    RAISE EXCEPTION 'Invalid physical command actor context or evidence' USING ERRCODE='23514';END IF;
  SELECT * INTO h FROM physical_location_state WHERE physical_id=p.id FOR UPDATE;
  n:=h; n.version:=h.version+1;
  NEW.before_state:=to_jsonb(h)::text;
  IF NEW.kind='activate' THEN
    IF h.version<>0 OR NEW.ref_id<>p.review_id OR u.role<>'admin' THEN RAISE EXCEPTION 'Invalid activation' USING ERRCODE='23514';END IF;
    SELECT * INTO r FROM physical_sharing_reviews WHERE id=p.review_id AND status='confirmed' AND confirmed_by=u.id;
    IF r.id IS NULL THEN RAISE EXCEPTION 'Confirmed physical activation required' USING ERRCODE='23514';END IF;
    SELECT s.* INTO stk FROM stock s JOIN storage_contexts c ON c.id=s.context_id WHERE c.warehouse_id=p.warehouse_id AND lower(btrim(s.local))=p.code AND s.quantidade>0;
    IF stk.id IS NULL THEN n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
    ELSE
      IF NEW.stock_id IS DISTINCT FROM stk.id OR NEW.context_id<>stk.context_id OR stk.quantidade<>1 THEN RAISE EXCEPTION 'Physical baseline mismatch' USING ERRCODE='23514';END IF;
      n.state:='occupied';n.context_id:=stk.context_id;n.stock_id:=stk.id;n.source_kind:='stock';n.source_id:=stk.id;
    END IF;
  ELSIF NEW.kind IN('putaway_hold','putaway_commit','putaway_cancel') THEN
    SELECT * INTO put FROM putaways WHERE id=NEW.ref_id AND context_id=NEW.context_id AND local=p.label AND quantidade=1 AND NOT stacked;
    IF put.id IS NULL THEN RAISE EXCEPTION 'Physical putaway mismatch' USING ERRCODE='23514';END IF;
    IF NEW.kind='putaway_hold' THEN
      IF h.state<>'free' OR put.status<>'draft' OR put.created_by<>u.id THEN RAISE EXCEPTION 'Physical position already assigned' USING ERRCODE='23514';END IF;
      n.state:='pending_putaway';n.context_id:=NEW.context_id;n.stock_id:=NULL;n.source_kind:='putaway';n.source_id:=put.id;
    ELSE
      IF h.state<>'pending_putaway' OR h.context_id<>NEW.context_id OR h.source_id<>put.id THEN RAISE EXCEPTION 'Physical pending assignment changed' USING ERRCODE='23514';END IF;
      IF NEW.kind='putaway_cancel' THEN
        IF put.status<>'cancelled' OR u.role<>'admin' THEN RAISE EXCEPTION 'Proven administrative cancellation required' USING ERRCODE='23514';END IF;
        n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
      ELSE
        IF put.status<>'confirmed' OR put.created_by<>u.id OR NEW.stock_id IS DISTINCT FROM put.stock_id THEN RAISE EXCEPTION 'Confirmed physical putaway required' USING ERRCODE='23514';END IF;
        SELECT * INTO stk FROM stock WHERE id=put.stock_id AND context_id=NEW.context_id AND local=p.label AND quantidade=1;
        IF stk.id IS NULL THEN RAISE EXCEPTION 'Missing physical stock' USING ERRCODE='23514';END IF;
        n.state:='occupied';n.stock_id:=put.stock_id;n.source_kind:='stock';n.source_id:=put.stock_id;
      END IF;
    END IF;
  ELSIF NEW.kind IN('transfer_hold','transfer_in','transfer_out') THEN
    SELECT * INTO tr FROM stock_transfers WHERE id=NEW.ref_id AND context_id=NEW.context_id AND quantidade=1 AND NOT stacked AND created_by=u.id;
    IF tr.id IS NULL THEN RAISE EXCEPTION 'Physical transfer mismatch' USING ERRCODE='23514';END IF;
    IF NEW.kind='transfer_hold' THEN
      IF tr.status<>'draft' OR tr.destination_local<>p.label OR h.state<>'free' THEN RAISE EXCEPTION 'Physical transfer destination unavailable' USING ERRCODE='23514';END IF;
      n.state:='pending_transfer';n.context_id:=NEW.context_id;n.stock_id:=NULL;n.source_kind:='transfer';n.source_id:=tr.id;
    ELSIF NEW.kind='transfer_in' THEN
      IF tr.status<>'confirmed' OR tr.destination_local<>p.label OR h.state<>'pending_transfer' OR h.source_id<>tr.id OR h.context_id<>NEW.context_id OR NEW.stock_id IS DISTINCT FROM tr.destination_stock_id THEN
        RAISE EXCEPTION 'Physical transfer confirmation mismatch' USING ERRCODE='23514';END IF;
      n.state:='occupied';n.stock_id:=tr.destination_stock_id;n.source_kind:='stock';n.source_id:=tr.destination_stock_id;
    ELSE
      IF tr.status<>'confirmed' OR tr.source_local<>p.label OR h.state<>'occupied' OR h.stock_id<>tr.source_stock_id OR h.context_id<>NEW.context_id THEN
        RAISE EXCEPTION 'Physical transfer source changed' USING ERRCODE='23514';END IF;
      n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
    END IF;
  ELSIF NEW.kind IN('dispatch_pending','dispatch_release') THEN
    SELECT * INTO ord FROM dispatch_orders WHERE id=NEW.ref_id AND context_id=NEW.context_id AND status='confirmed';
    IF ord.id IS NULL OR NOT EXISTS(SELECT 1 FROM dispatch_picks k JOIN dispatch_lines l ON l.id=k.line_id AND l.context_id=k.context_id
      WHERE l.order_id=ord.id AND k.context_id=NEW.context_id AND k.stock_id=NEW.stock_id AND k.local=p.label AND k.quantidade=1) THEN
      RAISE EXCEPTION 'Physical dispatch reference mismatch' USING ERRCODE='23514';END IF;
    IF NEW.kind='dispatch_pending' THEN
      IF NOT ord.office OR h.state<>'occupied' OR h.context_id<>NEW.context_id OR h.stock_id IS DISTINCT FROM NEW.stock_id OR ord.created_by<>u.id THEN
        RAISE EXCEPTION 'Physical accounting dispatch mismatch' USING ERRCODE='23514';END IF;
      n.state:='awaiting_collection';n.source_kind:='dispatch';n.source_id:=ord.id;
    ELSE
      IF h.context_id<>NEW.context_id OR h.stock_id IS DISTINCT FROM NEW.stock_id THEN RAISE EXCEPTION 'Physical release cannot affect another pallet' USING ERRCODE='23514';END IF;
      IF ord.office THEN
        IF h.state<>'awaiting_collection' OR h.source_id<>ord.id OR u.role<>'admin' OR NOT EXISTS(SELECT 1 FROM preparation_completions WHERE order_id=ord.id AND context_id=NEW.context_id AND actor=u.username) THEN
          RAISE EXCEPTION 'Explicit proven physical collection required' USING ERRCODE='23514';END IF;
      ELSE
        IF h.state<>'occupied' OR ord.checked_by IS NULL OR ord.created_by<>u.id OR EXISTS(SELECT 1 FROM dispatch_picks k JOIN dispatch_lines l ON l.id=k.line_id WHERE l.order_id=ord.id AND k.actual IS DISTINCT FROM k.quantidade) THEN
          RAISE EXCEPTION 'Physical conference required' USING ERRCODE='23514';END IF;
      END IF;
      IF EXISTS(SELECT 1 FROM stock WHERE id=NEW.stock_id AND quantidade<>0) THEN RAISE EXCEPTION 'Physical release still has accounting stock' USING ERRCODE='23514';END IF;
      n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
    END IF;
  ELSIF NEW.kind IN('inventory_hold','inventory_commit') THEN
    SELECT * INTO inv FROM physical_inventories WHERE id=NEW.ref_id AND context_id=NEW.context_id;
    SELECT * INTO scope FROM inventory_scopes WHERE inventory_id=inv.id AND context_id=NEW.context_id AND local=p.label AND counted;
    IF inv.id IS NULL OR scope.id IS NULL OR u.role<>'admin' OR EXISTS(SELECT 1 FROM inventory_counts WHERE scope_id=scope.id AND pending) THEN
      RAISE EXCEPTION 'Approved identified physical count required' USING ERRCODE='23514';END IF;
    IF NEW.kind='inventory_hold' THEN
      IF inv.status<>'submitted' OR (h.state<>'free' AND (h.context_id IS DISTINCT FROM NEW.context_id OR h.state NOT IN('occupied','awaiting_collection')))
        OR scope.clock IS DISTINCT FROM (SELECT version FROM location_clocks WHERE context_id=NEW.context_id AND local=p.label) THEN
        RAISE EXCEPTION 'Physical inventory reference changed or held' USING ERRCODE='23514';END IF;
      n.previous:=to_jsonb(h)::text;n.state:='pending_inventory';n.context_id:=NEW.context_id;n.source_kind:='inventory';n.source_id:=inv.id;
    ELSE
      IF inv.status<>'applied' OR h.state<>'pending_inventory' OR h.source_id<>inv.id OR h.context_id<>NEW.context_id THEN RAISE EXCEPTION 'Physical inventory confirmation mismatch' USING ERRCODE='23514';END IF;
      SELECT coalesce(sum(quantidade),0) INTO total FROM inventory_counts WHERE scope_id=scope.id;
      IF total>1 OR (SELECT coalesce(sum(quantidade),0) FROM stock WHERE context_id=NEW.context_id AND local=p.label)<>total THEN RAISE EXCEPTION 'Physical count and stock mismatch' USING ERRCODE='23514';END IF;
      IF total=0 THEN n.state:='free';n.context_id:=NULL;n.stock_id:=NULL;n.source_kind:=NULL;n.source_id:=NULL;
      ELSE
        SELECT * INTO stk FROM stock WHERE context_id=NEW.context_id AND local=p.label AND quantidade=1;
        IF stk.id IS NULL OR stk.stacked THEN RAISE EXCEPTION 'Physical inventory identity mismatch' USING ERRCODE='23514';END IF;
        IF (h.previous::jsonb->>'state')='awaiting_collection' AND NOT EXISTS(SELECT 1 FROM preparation_compensations WHERE order_id=(h.previous::jsonb->>'source_id')::integer AND inventory_id=inv.id AND context_id=NEW.context_id) THEN
          RAISE EXCEPTION 'Cannot reinstate printed accounting dispatch without compensation' USING ERRCODE='23514';END IF;
        n.state:='occupied';n.stock_id:=stk.id;n.source_kind:='stock';n.source_id:=stk.id;
      END IF;
      n.previous:=NULL;
    END IF;
  ELSE RAISE EXCEPTION 'Unknown physical command' USING ERRCODE='23514';
  END IF;
  NEW.after_state:=to_jsonb(n)::text;
  UPDATE physical_location_state SET state=n.state,context_id=n.context_id,stock_id=n.stock_id,source_kind=n.source_kind,source_id=n.source_id,previous=n.previous,version=n.version WHERE physical_id=p.id;
  INSERT INTO location_clocks(context_id,local,version) SELECT context_id,p.label,1 FROM physical_location_access WHERE physical_id=p.id
    ON CONFLICT(context_id,local) DO UPDATE SET version=location_clocks.version+1;
  RETURN NEW;
END $$;
CREATE TRIGGER physical_event_apply BEFORE INSERT ON physical_location_events FOR EACH ROW EXECUTE FUNCTION ddn_shared_event();
CREATE FUNCTION ddn_shared_projection_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_name text;
BEGIN
  SELECT pg_get_userbyid(relowner) INTO owner_name FROM pg_class WHERE oid=TG_RELID;
  IF TG_RELID<>'physical_location_state'::regclass OR TG_OP='DELETE' OR pg_trigger_depth()<2 OR current_user<>owner_name THEN RAISE EXCEPTION 'Physical projection is read only' USING ERRCODE='23514';END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER physical_projection_guard BEFORE INSERT OR UPDATE OR DELETE ON physical_location_state FOR EACH ROW EXECUTE FUNCTION ddn_shared_projection_guard();
CREATE TRIGGER physical_locations_immutable BEFORE UPDATE OR DELETE ON physical_locations FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_access_immutable BEFORE UPDATE OR DELETE ON physical_location_access FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_events_immutable BEFORE UPDATE OR DELETE ON physical_location_events FOR EACH ROW EXECUTE FUNCTION wms_append_only();

CREATE FUNCTION ddn_shared_putaway_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE pos physical_locations; current_record putaways;
BEGIN
  SELECT * INTO current_record FROM putaways WHERE id=NEW.id;
  SELECT p.* INTO pos FROM physical_locations p JOIN storage_contexts c ON c.warehouse_id=p.warehouse_id WHERE c.id=current_record.context_id AND p.code=lower(btrim(current_record.local));
  IF NOT FOUND THEN RETURN NEW;END IF;
  IF current_record.quantidade<>1 OR current_record.stacked THEN RAISE EXCEPTION 'Shared rack putaway requires one normal pallet' USING ERRCODE='23514';END IF;
  IF current_record.status='draft' THEN
    IF NOT EXISTS(SELECT 1 FROM physical_location_state WHERE physical_id=pos.id AND state='pending_putaway' AND context_id=current_record.context_id AND source_id=current_record.id) THEN
      RAISE EXCEPTION 'Physical putaway pending assignment required' USING ERRCODE='23514';END IF;
  ELSIF NOT EXISTS(SELECT 1 FROM physical_location_events WHERE physical_id=pos.id AND context_id=current_record.context_id AND ref_id=current_record.id
    AND kind=CASE WHEN current_record.status='confirmed' THEN 'putaway_commit' ELSE 'putaway_cancel' END) THEN
    RAISE EXCEPTION 'Physical putaway lifecycle event required' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER shared_putaway_complete AFTER INSERT OR UPDATE ON putaways DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ddn_shared_putaway_complete();
