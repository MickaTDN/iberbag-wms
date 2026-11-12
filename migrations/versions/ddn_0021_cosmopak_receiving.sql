CREATE FUNCTION ddn_cosmo_projection() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE owner_name text;
BEGIN
 SELECT pg_get_userbyid(relowner) INTO owner_name FROM pg_class WHERE oid=TG_RELID;
 IF current_user<>owner_name OR pg_trigger_depth()<2 OR TG_OP='DELETE' THEN RAISE EXCEPTION 'Cosmopak projection read only' USING ERRCODE='23514';END IF;
 IF TG_TABLE_NAME='cosmo_receptions' THEN
 IF (NEW.id,NEW.context_id,NEW.request_key,NEW.source_name,NEW.source_sha256,NEW.source_excel,NEW.report,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.context_id,OLD.request_key,OLD.source_name,OLD.source_sha256,OLD.source_excel,OLD.report,OLD.created_by,OLD.created_at) THEN RAISE EXCEPTION 'Cosmopak source immutable' USING ERRCODE='23514';END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cosmo_state_protected BEFORE INSERT OR UPDATE OR DELETE ON cosmo_states FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_projection();
CREATE TRIGGER cosmo_reception_protected BEFORE UPDATE OR DELETE ON cosmo_receptions FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_projection();
CREATE FUNCTION ddn_cosmo_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r cosmo_receptions;j jsonb;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN clients u ON u.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.client_id=3 AND c.warehouse_id=1 AND c.active AND u.active AND w.active) THEN RAISE EXCEPTION 'Cosmopak Ovar context required' USING ERRCODE='23514';END IF;
 IF TG_TABLE_NAME='cosmo_receptions' THEN
  IF NEW.version<>1 OR NEW.status<>'planned' OR NEW.receipt_id IS NOT NULL OR NEW.guide IS NOT NULL OR NEW.arrived_on IS NOT NULL
   OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role='admin')
   OR NEW.unloaded_by IS NOT NULL OR NEW.unloaded_at IS NOT NULL OR NEW.legacy_code IS NOT NULL
   OR NEW.source_sha256<>encode(sha256(NEW.source_excel),'hex') OR NEW.report::jsonb->>'sha256'<>NEW.source_sha256
   OR NEW.report::jsonb->>'sheet'<>'Camião DDN' OR octet_length(NEW.source_excel)>20971520 THEN RAISE EXCEPTION 'Verified planned Cosmopak source required' USING ERRCODE='23514';END IF;
 ELSE
  SELECT * INTO r FROM cosmo_receptions WHERE id=NEW.reception_id AND context_id=NEW.context_id;
  SELECT x INTO j FROM jsonb_array_elements(r.report::jsonb->'rows') x WHERE (x->>'row')::integer=NEW.source_row;
  IF r.status<>'planned' OR r.version<>1 OR jsonb_array_length(r.report::jsonb->'issues')<>0 OR j IS NULL
   OR NEW.ordinal>(j->>'pallets')::integer OR (NEW.article,NEW.description,NEW.lot,NEW.boxes,NEW.incomplete) IS DISTINCT FROM (j->>'article',j->>'description',j->>'lot',(j->>'boxes')::integer,(j->>'incomplete')::boolean) THEN RAISE EXCEPTION 'Pallet must match immutable Excel row' USING ERRCODE='23514';END IF;
 END IF;RETURN NEW;
END $$;
CREATE TRIGGER cosmo_source_guard BEFORE INSERT ON cosmo_receptions FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_guard();
CREATE TRIGGER cosmo_pallet_guard BEFORE INSERT ON cosmo_pallets FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_guard();
CREATE FUNCTION ddn_cosmo_initial() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 IF TG_RELID<>'cosmo_pallets'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted Cosmopak initializer' USING ERRCODE='23514';END IF;
 INSERT INTO cosmo_states(context_id,pallet_id,status,decision,version) VALUES(NEW.context_id,NEW.id,'planned','unresolved',0);RETURN NEW;
END $$;
CREATE TRIGGER cosmo_initialize AFTER INSERT ON cosmo_pallets FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_initial();
CREATE FUNCTION ddn_cosmo_operation() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE a cosmo_pallets;b cosmo_states;n cosmo_states;r cosmo_receptions;u users;master physical_locations;h physical_location_state;rec receipts;expected jsonb;
BEGIN
 IF TG_RELID<>'cosmo_operations'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted Cosmopak operation' USING ERRCODE='23514';END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO a FROM cosmo_pallets WHERE id=NEW.pallet_id AND context_id=NEW.context_id;
 SELECT * INTO r FROM cosmo_receptions WHERE id=a.reception_id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO b FROM cosmo_states WHERE pallet_id=a.id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO u FROM users WHERE id=NEW.actor_id AND active AND role IN ('admin','operador');
 IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN clients o ON o.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.client_id=3 AND c.warehouse_id=1 AND c.active AND o.active AND w.active) OR a.id IS NULL OR u.id IS NULL OR r.status='approved' OR b.version<>NEW.expected_version OR b.status='approved' OR length(btrim(NEW.reason)) NOT BETWEEN 1 AND 2000 THEN RAISE EXCEPTION 'Stale or unauthorized Cosmopak operation' USING ERRCODE='23514';END IF;
 n:=b;n.version:=b.version+1;NEW.before_state:=to_jsonb(b)::text;
 IF NEW.kind='place' THEN
  SELECT p.* INTO master FROM physical_locations p JOIN physical_location_access x ON x.physical_id=p.id WHERE x.context_id=NEW.context_id AND p.label=NEW.local;
  IF master.id IS NULL OR NOT EXISTS(SELECT 1 FROM locations WHERE context_id=NEW.context_id AND codigo=NEW.local AND active AND capacidade=1) THEN RAISE EXCEPTION 'Configured shared rack required; no floor' USING ERRCODE='23514';END IF;
  SELECT * INTO h FROM physical_location_state WHERE physical_id=master.id;
  IF h.state<>'free' AND NOT(h.context_id=NEW.context_id AND h.source_kind='cosmopak' AND h.source_id=a.id AND h.state='pending_putaway') THEN RAISE EXCEPTION 'Physical position already occupied' USING ERRCODE='23514';END IF;
  IF b.local IS NOT NULL AND b.local<>NEW.local AND NOT NEW.vacated THEN RAISE EXCEPTION 'Prove old position physically empty before correction' USING ERRCODE='23514';END IF;
  IF NEW.decision IS NOT NULL AND NEW.decision<>'unresolved' THEN RAISE EXCEPTION 'Operator cannot preapprove a discrepancy' USING ERRCODE='23514';END IF;
  IF NEW.actual_boxes IS NOT NULL AND NEW.actual_boxes<0 THEN RAISE EXCEPTION 'Invalid actual box count' USING ERRCODE='23514';END IF;
  n.local:=NEW.local;n.actual_boxes:=coalesce(NEW.actual_boxes,b.actual_boxes,a.boxes);n.status:='placed';n.decision:=CASE WHEN n.actual_boxes=a.boxes AND NEW.decision IS NULL THEN 'accept' ELSE 'unresolved' END;
 ELSIF NEW.kind='missing' THEN
  n.status:=CASE WHEN b.local IS NULL THEN 'missing' ELSE 'blocked' END;n.decision:='unresolved';n.actual_boxes:=NULL;
 ELSIF NEW.kind='decide' THEN
  IF u.role<>'admin' OR NOT((NEW.decision='accept' AND b.status='placed' AND b.actual_boxes IS NOT NULL) OR (NEW.decision='exclude' AND b.status='missing' AND b.local IS NULL)) THEN RAISE EXCEPTION 'Administrative explicit safe decision required' USING ERRCODE='23514';END IF;
  n.decision:=NEW.decision;
 ELSIF NEW.kind='approve' THEN
  SELECT * INTO rec FROM receipts WHERE id=NEW.receipt_id AND context_id=NEW.context_id AND status='confirmed';
  SELECT x INTO expected FROM cosmo_reviews v CROSS JOIN LATERAL jsonb_array_elements(v.payload::jsonb->'pallets') x WHERE v.reception_id=r.id AND v.kind='review' AND v.context_id=NEW.context_id AND v.expected_version=r.version-1 AND (x->>'id')::integer=a.id ORDER BY v.id DESC LIMIT 1;
  SELECT p.* INTO master FROM physical_locations p JOIN physical_location_access x ON x.physical_id=p.id WHERE x.context_id=NEW.context_id AND p.label=b.local;
  IF u.role<>'admin' OR r.status<>'reviewed' OR b.status<>'placed' OR b.decision<>'accept' OR rec.id IS NULL OR expected IS NULL
   OR (expected->>'version')::integer<>b.version OR (expected->>'physical_version')::integer IS DISTINCT FROM (SELECT version FROM physical_location_state WHERE physical_id=master.id)
   OR rec.received_on IS DISTINCT FROM r.arrived_on OR NOT EXISTS(SELECT 1 FROM stock WHERE id=NEW.stock_id AND context_id=NEW.context_id AND artigo=a.article AND lote=a.lot AND carga=rec.code AND local=b.local AND quantidade=1 AND NOT stacked) THEN RAISE EXCEPTION 'Revalidated reviewed Cosmopak pallet and receipt required' USING ERRCODE='23514';END IF;
  n.status:='approved';n.stock_id:=NEW.stock_id;
 ELSE RAISE EXCEPTION 'Invalid Cosmopak command' USING ERRCODE='23514';END IF;
 NEW.after_state:=to_jsonb(n)::text;
 UPDATE cosmo_states SET status=n.status,local=n.local,actual_boxes=n.actual_boxes,decision=n.decision,stock_id=n.stock_id,version=n.version WHERE id=b.id;
 IF NEW.kind<>'approve' THEN UPDATE cosmo_receptions SET version=version+1,status=CASE WHEN status='reviewed' THEN 'unloaded' ELSE status END WHERE id=r.id;END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cosmo_apply_operation BEFORE INSERT ON cosmo_operations FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_operation();

CREATE FUNCTION ddn_cosmo_physical() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE b cosmo_states;master physical_locations;u users;
BEGIN
 IF TG_RELID<>'cosmo_operations'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted Cosmopak physical trigger' USING ERRCODE='23514';END IF;
 SELECT * INTO b FROM jsonb_populate_record(NULL::cosmo_states,NEW.before_state::jsonb);
 SELECT p.* INTO master FROM physical_locations p JOIN physical_location_access x ON x.physical_id=p.id WHERE x.context_id=NEW.context_id AND p.label=coalesce(NEW.local,b.local);
 SELECT * INTO u FROM users WHERE id=NEW.actor_id;
 IF NEW.kind='place' THEN
  IF b.local IS NOT NULL AND b.local<>NEW.local THEN
   INSERT INTO physical_location_events(context_id,physical_id,kind,ref_id,actor_id,reason,before_state,after_state,created_at) SELECT NEW.context_id,id,'cosmo_vacate',NEW.id,u.id,NEW.reason,'','',NEW.created_at FROM physical_locations WHERE warehouse_id=1 AND label=b.local;
  END IF;
  INSERT INTO physical_location_events(context_id,physical_id,kind,ref_id,actor_id,reason,before_state,after_state,created_at) VALUES(NEW.context_id,master.id,'cosmo_place',NEW.id,u.id,NEW.reason,'','',NEW.created_at);
 ELSIF NEW.kind='approve' THEN
  INSERT INTO physical_location_events(context_id,physical_id,kind,ref_id,stock_id,actor_id,reason,before_state,after_state,created_at) VALUES(NEW.context_id,master.id,'cosmo_approve',NEW.id,NEW.stock_id,u.id,NEW.reason,'','',NEW.created_at);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cosmo_apply_physical AFTER INSERT ON cosmo_operations FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_physical();
CREATE FUNCTION ddn_cosmo_review() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE r cosmo_receptions;u users;j jsonb;rec receipts;planned integer;
BEGIN
 IF TG_RELID<>'cosmo_reviews'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted Cosmopak reception command' USING ERRCODE='23514';END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO r FROM cosmo_receptions WHERE id=NEW.reception_id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO u FROM users WHERE id=NEW.actor_id AND active AND role IN('admin','operador');
 j:=NEW.payload::jsonb;
 IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN clients o ON o.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.client_id=3 AND c.warehouse_id=1 AND c.active AND o.active AND w.active) OR r.id IS NULL OR u.id IS NULL OR r.status='approved' OR r.version<>NEW.expected_version OR length(btrim(NEW.reason)) NOT BETWEEN 1 AND 2000
  OR (NEW.kind<>'unloaded' AND u.role<>'admin') THEN RAISE EXCEPTION 'Stale or unauthorized reception revision' USING ERRCODE='23514';END IF;
 IF NEW.kind='metadata' THEN
  IF coalesce(length(btrim(j->>'guide')),0) NOT BETWEEN 1 AND 120 OR j->>'arrived_on' IS NULL OR (length(j->>'arrived_on')<>10) OR ((j->>'arrived_on')::date<'2027-01-01' AND coalesce(length(btrim(j->>'legacy_code')),0) NOT BETWEEN 1 AND 120) THEN RAISE EXCEPTION 'Actual arrival guide and historical reference required' USING ERRCODE='23514';END IF;
  UPDATE cosmo_receptions SET guide=j->>'guide',arrived_on=(j->>'arrived_on')::date,legacy_code=j->>'legacy_code',version=version+1,status=CASE WHEN status='reviewed' THEN 'unloaded' ELSE status END WHERE id=r.id;
 ELSIF NEW.kind='unloaded' THEN
  IF NOT EXISTS(SELECT 1 FROM cosmo_pallets WHERE reception_id=r.id) OR EXISTS(SELECT 1 FROM cosmo_states s JOIN cosmo_pallets p ON p.id=s.pallet_id WHERE p.reception_id=r.id AND s.status='planned') THEN RAISE EXCEPTION 'Classify every planned pallet, including missing' USING ERRCODE='23514';END IF;
  UPDATE cosmo_receptions SET status='unloaded',unloaded_by=u.id,unloaded_at=NEW.created_at,version=version+1 WHERE id=r.id;
 ELSIF NEW.kind='review' THEN
  IF r.status<>'unloaded' OR r.unloaded_by IS NULL OR r.guide IS NULL OR r.arrived_on IS NULL OR (j->>'version')::integer<>r.version
   OR (j->>'reception_id')::integer<>r.id OR j->>'guide'<>r.guide OR (j->>'arrived_on')::date<>r.arrived_on::date
   OR jsonb_array_length(j->'pallets')<>(SELECT count(*) FROM cosmo_pallets WHERE reception_id=r.id)
   OR EXISTS(SELECT 1 FROM cosmo_states s JOIN cosmo_pallets p ON p.id=s.pallet_id WHERE p.reception_id=r.id AND NOT((s.status='placed' AND s.decision='accept') OR(s.status='missing' AND s.decision='exclude' AND s.local IS NULL))) THEN RAISE EXCEPTION 'Explicit resolved unloading review required' USING ERRCODE='23514';END IF;
  UPDATE cosmo_receptions SET status='reviewed',version=version+1 WHERE id=r.id;
 ELSIF NEW.kind='approve' THEN
  SELECT * INTO rec FROM receipts WHERE id=(j->>'receipt_id')::integer AND context_id=NEW.context_id AND status='confirmed' AND created_by=u.id;
  IF r.status<>'reviewed' OR rec.id IS NULL OR rec.received_on IS DISTINCT FROM r.arrived_on
    OR EXISTS(SELECT 1 FROM cosmo_states s JOIN cosmo_pallets p ON p.id=s.pallet_id WHERE p.reception_id=r.id AND NOT((s.status='approved' AND s.stock_id IS NOT NULL)OR(s.status='missing' AND s.decision='exclude' AND s.local IS NULL)))
    OR (SELECT count(*) FROM cosmo_states s JOIN cosmo_pallets p ON p.id=s.pallet_id WHERE p.reception_id=r.id AND s.status='approved') IS DISTINCT FROM (SELECT coalesce(sum(quantidade),0) FROM receipt_lines WHERE receipt_id=rec.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Integral approved reception reconciliation required' USING ERRCODE='23514';END IF;
  UPDATE cosmo_receptions SET status='approved',receipt_id=rec.id,version=version+1 WHERE id=r.id;
 ELSE RAISE EXCEPTION 'Unknown reception command' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER cosmo_apply_review BEFORE INSERT ON cosmo_reviews FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_review();
CREATE FUNCTION ddn_cosmo_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r cosmo_receptions;
BEGIN
 SELECT * INTO r FROM cosmo_receptions WHERE id=NEW.id;
 IF jsonb_array_length(r.report::jsonb->'issues')=0 AND (r.report::jsonb->>'pallets')::integer<>(SELECT count(*) FROM cosmo_pallets WHERE reception_id=r.id AND context_id=r.context_id) THEN RAISE EXCEPTION 'Incomplete Cosmopak import' USING ERRCODE='23514';END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER cosmo_plan_complete AFTER INSERT ON cosmo_receptions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_complete();
CREATE FUNCTION ddn_cosmo_stock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM cosmo_states WHERE stock_id=OLD.id AND context_id=OLD.context_id AND status='approved') THEN
  IF TG_OP='DELETE' OR NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'Cosmopak stock requires future authorized movement; no direct edits' USING ERRCODE='23514';END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD;END IF;RETURN NEW;
END $$;
CREATE TRIGGER cosmo_stock_history BEFORE UPDATE OR DELETE ON stock FOR EACH ROW EXECUTE FUNCTION ddn_cosmo_stock();
CREATE TRIGGER cosmo_pallets_immutable BEFORE UPDATE OR DELETE ON cosmo_pallets FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_operations_immutable BEFORE UPDATE OR DELETE ON cosmo_operations FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_reviews_immutable BEFORE UPDATE OR DELETE ON cosmo_reviews FOR EACH ROW EXECUTE FUNCTION wms_append_only();

CREATE TRIGGER cosmo_receptions_no_truncate BEFORE TRUNCATE ON cosmo_receptions FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();

CREATE TRIGGER cosmo_pallets_no_truncate BEFORE TRUNCATE ON cosmo_pallets FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();

CREATE TRIGGER cosmo_operations_no_truncate BEFORE TRUNCATE ON cosmo_operations FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();

CREATE TRIGGER cosmo_reviews_no_truncate BEFORE TRUNCATE ON cosmo_reviews FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();

CREATE TRIGGER cosmo_states_no_truncate BEFORE TRUNCATE ON cosmo_states FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
