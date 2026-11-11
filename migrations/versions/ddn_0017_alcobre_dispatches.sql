CREATE FUNCTION alcobre_current_event(unit_id integer) RETURNS integer LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$ SELECT id FROM alcobre_dispatch_events WHERE bobine_id=unit_id ORDER BY id DESC LIMIT 1 $$;
CREATE FUNCTION alcobre_current_state(unit_id integer) RETURNS text LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$ SELECT coalesce((SELECT state FROM alcobre_dispatch_events WHERE bobine_id=unit_id ORDER BY id DESC LIMIT 1),'available') $$;
CREATE FUNCTION alcobre_reserved_guide(unit_id integer) RETURNS integer LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$ SELECT o.dispatch_id FROM alcobre_dispatch_events e JOIN alcobre_dispatch_operations o ON o.id=e.operation_id AND o.context_id=e.context_id WHERE e.bobine_id=unit_id AND e.state='reserved' AND e.id=alcobre_current_event(unit_id) $$;

CREATE FUNCTION alcobre_dispatch_plan(requested jsonb,guide_id integer,ctx integer,reference text) RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE item jsonb; result jsonb='[]'; u record; st text; why text; pending boolean;
BEGIN
 IF coalesce(jsonb_typeof(requested),'null')<>'array' OR jsonb_array_length(requested) NOT BETWEEN 1 AND 300 THEN RAISE EXCEPTION 'Exact bobine list required'; END IF;
 IF (SELECT count(*) FROM jsonb_array_elements(requested))<>(SELECT count(DISTINCT lower(x #>> '{}')) FROM jsonb_array_elements(requested) x) THEN RAISE EXCEPTION 'Repeated requested number'; END IF;
 SELECT EXISTS(SELECT 1 FROM alcobre_entries e WHERE e.context_id=ctx AND e.status='confirmed' AND e.mode='pending' AND e.quantity>(SELECT count(*) FROM alcobre_bobines b WHERE b.entry_id=e.id AND b.context_id=ctx)) INTO pending;
 FOR item IN SELECT * FROM jsonb_array_elements(requested) LOOP
  IF jsonb_typeof(item)<>'string' OR length(item #>> '{}') NOT BETWEEN 1 AND 120 OR item #>> '{}'<>btrim(item #>> '{}') OR item #>> '{}' ~ '[[:cntrl:]]' THEN RAISE EXCEPTION 'Preserve exact text numbers'; END IF;
  SELECT b.id,b.number,b.entry_id,p.id AS placement_id,p.local INTO u FROM alcobre_bobines b LEFT JOIN alcobre_placements p ON p.bobine_id=b.id AND p.context_id=ctx WHERE b.context_id=ctx AND (b.number_key=lower(item #>> '{}') OR lower(b.number)=lower(item #>> '{}'));
  st=CASE WHEN u.id IS NULL THEN NULL ELSE alcobre_current_state(u.id) END;
  why=CASE WHEN u.id IS NULL THEN CASE WHEN pending THEN 'unknown_pending' ELSE 'unknown' END WHEN u.placement_id IS NULL THEN 'unlocated' ELSE st END;
  IF st='reserved' THEN why=CASE WHEN alcobre_reserved_guide(u.id)=guide_id THEN 'reserved_self' ELSE 'reserved_other' END; END IF;
  IF why IN ('available','reserved_self') AND NOT EXISTS(SELECT 1 FROM locations l JOIN alcobre_slots s ON s.local=l.codigo AND s.context_id=ctx JOIN alcobre_zones z ON z.id=s.zone_id AND z.context_id=ctx WHERE l.context_id=ctx AND l.codigo=u.local AND l.active AND l.capacidade=1 AND z.active) THEN why='inactive'; END IF;
  result=result||jsonb_build_array(jsonb_build_object('requested',item,'bobine_id',u.id,'number',u.number,'entry_id',u.entry_id,'placement_id',u.placement_id,'local',u.local,'state',st,'before_event',CASE WHEN u.id IS NULL THEN NULL ELSE alcobre_current_event(u.id) END,'reason',why,'available',why IN ('available','reserved_self')));
 END LOOP;
 RETURN jsonb_build_object('reference',reference,'items',result);
END $$;

CREATE FUNCTION alcobre_dispatch_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE g record; r record; op_row record; who record; ev_row record; raw jsonb; body jsonb; snap jsonb; item jsonb; now_item jsonb; event_item jsonb; fresh jsonb; requested jsonb; n integer; expected integer=0; before_op integer; previous_prep integer; allowed boolean; unit_id integer; st text; target text; uid integer; collected jsonb; differences jsonb; outcome text; detail text; matched integer;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN alcobre_owners a ON a.client_id=c.client_id AND a.warehouse_id=c.warehouse_id JOIN clients cl ON cl.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND cl.active AND w.active) THEN RAISE EXCEPTION 'Alcobre context required'; END IF;
 IF TG_TABLE_NAME='alcobre_dispatches' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.version<>0 OR NEW.status<>'draft' OR NEW.active_revision IS NOT NULL OR octet_length(NEW.source_pdf) NOT BETWEEN 1 AND 5242880 OR NEW.source_digest<>encode(sha256(NEW.source_pdf),'hex') OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role='admin') THEN RAISE EXCEPTION 'Administrative original PDF draft required'; END IF;
  ELSE
   IF OLD.status IN ('confirmed','cancelled') OR (to_jsonb(NEW)-ARRAY['status','version','active_revision']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','version','active_revision']) THEN RAISE EXCEPTION 'Guide source/history immutable'; END IF;
   IF NEW.version=OLD.version+1 AND NEW.status=OLD.status AND NEW.active_revision IS NOT DISTINCT FROM OLD.active_revision THEN RETURN NEW; END IF;
   SELECT * INTO op_row FROM alcobre_dispatch_operations WHERE dispatch_id=NEW.id AND context_id=NEW.context_id ORDER BY id DESC LIMIT 1;
   IF NEW.version<>OLD.version OR op_row.id IS NULL OR (NEW.status='prepared' AND (op_row.kind<>'prepare' OR NEW.active_revision IS DISTINCT FROM op_row.revision_id)) OR (NEW.status IN ('confirmed','cancelled') AND NEW.active_revision IS DISTINCT FROM OLD.active_revision) OR (NEW.status='confirmed' AND op_row.kind<>'confirm') OR (NEW.status='cancelled' AND op_row.kind<>'cancel') OR NEW.status='draft' THEN RAISE EXCEPTION 'Guide transition requires atomic operation'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='alcobre_dispatch_events' THEN
  SELECT * INTO op_row FROM alcobre_dispatch_operations WHERE id=NEW.operation_id AND context_id=NEW.context_id;
  IF op_row.id IS NULL OR NEW.previous_id IS DISTINCT FROM alcobre_current_event(NEW.bobine_id) OR NOT EXISTS(SELECT 1 FROM alcobre_bobines WHERE id=NEW.bobine_id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Current original identity/event required'; END IF;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op_row.snapshot::jsonb->'events') e WHERE (e->>'bobine_id')::integer=NEW.bobine_id AND (e->>'previous_id')::integer IS NOT DISTINCT FROM NEW.previous_id AND e->>'state'=NEW.state) THEN RAISE EXCEPTION 'Event outside reviewed operation'; END IF;
  st=alcobre_current_state(NEW.bobine_id);
  IF NOT ((op_row.kind='prepare' AND NEW.state='reserved' AND st='available') OR (op_row.kind IN ('prepare','cancel') AND NEW.state='available' AND st='reserved' AND alcobre_reserved_guide(NEW.bobine_id)=op_row.dispatch_id) OR (op_row.kind='confirm' AND NEW.state IN ('shipped','blocked') AND st='reserved' AND alcobre_reserved_guide(NEW.bobine_id)=op_row.dispatch_id)) THEN RAISE EXCEPTION 'Illegal reservation/expedition transition'; END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='alcobre_dispatch_documents' THEN
  IF NEW.digest<>encode(sha256(NEW.pdf),'hex') OR octet_length(NEW.pdf) NOT BETWEEN 1 AND 4194304 OR (NEW.revision_id IS NULL)=(NEW.operation_id IS NULL) THEN RAISE EXCEPTION 'Immutable valid PDF origin required'; END IF;
  IF NEW.revision_id IS NOT NULL THEN SELECT d.* INTO g FROM alcobre_dispatch_revisions rv JOIN alcobre_dispatches d ON d.id=rv.dispatch_id AND d.context_id=rv.context_id WHERE rv.id=NEW.revision_id AND rv.context_id=NEW.context_id;
  ELSE SELECT d.* INTO g FROM alcobre_dispatch_operations o JOIN alcobre_dispatches d ON d.id=o.dispatch_id AND d.context_id=o.context_id WHERE o.id=NEW.operation_id AND o.context_id=NEW.context_id; END IF;
 ELSE SELECT * INTO g FROM alcobre_dispatches WHERE id=NEW.dispatch_id AND context_id=NEW.context_id; END IF;
 SELECT * INTO who FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador');
 IF g.id IS NULL OR who.id IS NULL OR (who.id<>g.created_by AND who.role<>'admin') THEN RAISE EXCEPTION 'Authorized active guide actor required'; END IF;
 IF TG_TABLE_NAME='alcobre_dispatch_documents' THEN RETURN NEW; END IF;
 IF g.status IN ('confirmed','cancelled') THEN RAISE EXCEPTION 'Guide already closed'; END IF;
 IF TG_TABLE_NAME='alcobre_dispatch_revisions' THEN
  raw=NEW.payload::jsonb;
  IF NEW.version<>g.version OR NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') OR btrim(NEW.reason)='' OR coalesce(jsonb_typeof(raw->'items'),'null')<>'array' THEN RAISE EXCEPTION 'Explicit immutable revision required'; END IF;
  SELECT jsonb_agg(x->'requested' ORDER BY ord) INTO requested FROM jsonb_array_elements(raw->'items') WITH ORDINALITY ar(x,ord);
  IF raw IS DISTINCT FROM alcobre_dispatch_plan(requested,g.id,g.context_id,raw->>'reference') THEN RAISE EXCEPTION 'Exact current numbers/stock required'; END IF;
  IF NOT NEW.manual AND (coalesce(g.extraction::jsonb->>'reliable','false')<>'true' OR requested IS DISTINCT FROM (SELECT jsonb_agg(x->'number' ORDER BY ord) FROM jsonb_array_elements(g.extraction::jsonb->'candidates') WITH ORDINALITY ar(x,ord))) THEN RAISE EXCEPTION 'Manual proven transcription required'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO r FROM alcobre_dispatch_revisions WHERE id=NEW.revision_id AND dispatch_id=g.id AND context_id=NEW.context_id;
 body=NEW.payload::jsonb;snap=NEW.snapshot::jsonb;
 SELECT max(id) INTO before_op FROM alcobre_dispatch_operations WHERE dispatch_id=g.id AND context_id=NEW.context_id;
 IF coalesce(jsonb_typeof(body->'version'),'null')<>'number' OR (body->>'version')::integer IS DISTINCT FROM g.version OR coalesce(jsonb_typeof(snap->'events'),'null')<>'array' OR coalesce(jsonb_typeof(snap->'plan'),'null')<>'object' OR (snap->>'before')::integer IS DISTINCT FROM before_op THEN RAISE EXCEPTION 'Current complete operation required'; END IF;
 IF r.id IS NULL AND NOT (NEW.kind='cancel' AND g.version=0 AND NEW.revision_id IS NULL) THEN RAISE EXCEPTION 'Guide revision required'; END IF;
 raw=coalesce(r.payload::jsonb,jsonb_build_object('reference',NULL,'items','[]'::jsonb));
 IF NEW.kind='prepare' THEN
  SELECT jsonb_agg(x->'requested' ORDER BY ord) INTO requested FROM jsonb_array_elements(raw->'items') WITH ORDINALITY ar(x,ord);
  IF r.version<>g.version OR body->>'digest' IS DISTINCT FROM r.digest OR g.active_revision IS NOT DISTINCT FROM r.id OR snap->'plan' IS DISTINCT FROM raw OR raw IS DISTINCT FROM alcobre_dispatch_plan(requested,g.id,g.context_id,raw->>'reference') THEN RAISE EXCEPTION 'Prepare current selection without substitutions'; END IF;
  SELECT count(*) INTO expected FROM alcobre_bobines b WHERE b.context_id=g.context_id AND alcobre_current_state(b.id)='reserved' AND alcobre_reserved_guide(b.id)=g.id AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(raw->'items') x WHERE (x->>'bobine_id')::integer=b.id AND (x->>'available')::boolean);
  expected=expected+(SELECT count(*) FROM jsonb_array_elements(raw->'items') x WHERE (x->>'available')::boolean AND x->>'state'='available');
 ELSIF NEW.kind='cancel' THEN
  IF (body->>'before')::integer IS DISTINCT FROM before_op OR snap->'plan' IS DISTINCT FROM raw OR coalesce(body->>'reason','')='' OR snap->>'reason' IS DISTINCT FROM body->>'reason' OR (r.id IS NOT NULL AND r.version<>g.version) THEN RAISE EXCEPTION 'Cancel current unconfirmed guide with reason'; END IF;
  SELECT count(*) INTO expected FROM alcobre_bobines b WHERE b.context_id=g.context_id AND alcobre_current_state(b.id)='reserved' AND alcobre_reserved_guide(b.id)=g.id;
 ELSIF NEW.kind='confirm' THEN
  IF g.status<>'prepared' OR r.id IS DISTINCT FROM g.active_revision OR r.version<>g.version OR (body->>'before')::integer IS DISTINCT FROM before_op OR NOT EXISTS(SELECT 1 FROM alcobre_dispatch_operations WHERE id=before_op AND kind='prepare') OR coalesce(jsonb_typeof(body->'collected'),'null')<>'array' OR coalesce(jsonb_typeof(body->'exceptions'),'null')<>'object' OR coalesce(jsonb_typeof(body->'loaded_on'),'null')<>'string' OR body->>'loaded_on' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR ((body->>'loaded_on')::date)::text IS DISTINCT FROM body->>'loaded_on' OR snap->>'loaded_on' IS DISTINCT FROM body->>'loaded_on' THEN RAISE EXCEPTION 'Single current complete guide confirmation required'; END IF;
  collected=body->'collected';differences=body->'exceptions';previous_prep=before_op;
  IF jsonb_array_length(collected)<>(SELECT count(DISTINCT x #>> '{}') FROM jsonb_array_elements(collected) x) THEN RAISE EXCEPTION 'Repeated collected number'; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(collected) x WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(raw->'items') y WHERE (y->>'available')::boolean AND y->'requested'=x)) THEN RAISE EXCEPTION 'Cannot ship SEM STOCK or unrequested bobine'; END IF;
  SELECT jsonb_agg(x->'requested' ORDER BY ord) INTO requested FROM jsonb_array_elements(raw->'items') WITH ORDINALITY ar(x,ord);
  fresh=alcobre_dispatch_plan(requested,g.id,g.context_id,raw->>'reference');
  IF snap->'plan'->>'reference' IS DISTINCT FROM raw->>'reference' OR coalesce(jsonb_typeof(snap->'plan'->'items'),'null')<>'array' OR jsonb_array_length(snap->'plan'->'items')<>jsonb_array_length(raw->'items') THEN RAISE EXCEPTION 'Preserve whole requested guide'; END IF;
  n=0;
  FOR item IN SELECT * FROM jsonb_array_elements(raw->'items') LOOP
   now_item=fresh->'items'->n;
   IF (item->>'available')::boolean THEN
    uid=(item->>'bobine_id')::integer;
    IF now_item->>'reason'<>'reserved_self' OR (item-ARRAY['state','before_event','reason']) IS DISTINCT FROM (now_item-ARRAY['state','before_event','reason']) THEN RAISE EXCEPTION 'Reserved identity/location changed'; END IF;
    IF item->>'state'='available' THEN
     IF NOT EXISTS(SELECT 1 FROM alcobre_dispatch_events WHERE id=alcobre_current_event(uid) AND operation_id=previous_prep) THEN RAISE EXCEPTION 'Reservation changed after preparation'; END IF;
    ELSIF alcobre_current_event(uid) IS DISTINCT FROM (item->>'before_event')::integer THEN RAISE EXCEPTION 'Old reservation changed'; END IF;
    IF collected ? (item->>'requested') THEN outcome='shipped';detail=NULL;
    ELSE
     outcome='exception';detail=differences->>(item->>'requested');
     IF coalesce(jsonb_typeof(differences->(item->>'requested')),'null')<>'string' OR btrim(coalesce(detail,''))='' OR length(detail)>2000 THEN RAISE EXCEPTION 'Each uncollected bobine needs evidence'; END IF;
    END IF;
    expected=expected+1;
   ELSE
    IF item IS DISTINCT FROM now_item THEN RAISE EXCEPTION 'SEM STOCK changed; revise explicitly'; END IF;
    outcome='exception';detail=NULL;
   END IF;
   IF (snap->'plan'->'items'->n)-ARRAY['outcome','difference'] IS DISTINCT FROM item OR snap->'plan'->'items'->n->>'outcome' IS DISTINCT FROM outcome OR ((item->>'available')::boolean AND snap->'plan'->'items'->n->>'difference' IS DISTINCT FROM detail) THEN RAISE EXCEPTION 'Archived outcome must preserve exact selection'; END IF;
   n=n+1;
  END LOOP;
  IF (SELECT count(*) FROM jsonb_object_keys(differences))<>expected-jsonb_array_length(collected) OR EXISTS(SELECT 1 FROM jsonb_object_keys(differences) k WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(raw->'items') x WHERE (x->>'available')::boolean AND x->>'requested'=k AND NOT collected ? k)) OR (snap->>'collected')::integer IS DISTINCT FROM jsonb_array_length(collected) OR (snap->>'exceptions')::integer IS DISTINCT FROM jsonb_array_length(raw->'items')-jsonb_array_length(collected) THEN RAISE EXCEPTION 'Whole guide totals/exception partition required'; END IF;
 END IF;
 IF jsonb_array_length(snap->'events')<>expected OR expected<>(SELECT count(DISTINCT (x->>'bobine_id')::integer) FROM jsonb_array_elements(snap->'events') x) THEN RAISE EXCEPTION 'All required events exactly once'; END IF;
 FOR event_item IN SELECT * FROM jsonb_array_elements(snap->'events') LOOP
  IF coalesce(jsonb_typeof(event_item->'bobine_id'),'null')<>'number' THEN RAISE EXCEPTION 'Original bobine required'; END IF;
  uid=(event_item->>'bobine_id')::integer;target=event_item->>'state';st=alcobre_current_state(uid);
  IF (event_item->>'previous_id')::integer IS DISTINCT FROM alcobre_current_event(uid) OR NOT EXISTS(SELECT 1 FROM alcobre_bobines WHERE id=uid AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Current original bobine required'; END IF;
  SELECT x INTO item FROM jsonb_array_elements(raw->'items') x WHERE (x->>'bobine_id')::integer=uid;
  allowed=false;
  IF NEW.kind='prepare' THEN allowed=(target='reserved' AND st='available' AND coalesce((item->>'available')::boolean,false)) OR (target='available' AND st='reserved' AND alcobre_reserved_guide(uid)=g.id AND NOT coalesce((item->>'available')::boolean,false));
  ELSIF NEW.kind='cancel' THEN allowed=target='available' AND st='reserved' AND alcobre_reserved_guide(uid)=g.id;
  ELSE allowed=coalesce((item->>'available')::boolean,false) AND st='reserved' AND alcobre_reserved_guide(uid)=g.id AND target=CASE WHEN collected ? (item->>'requested') THEN 'shipped' ELSE 'blocked' END; END IF;
  IF allowed IS DISTINCT FROM true THEN RAISE EXCEPTION 'Event not authorized by exact guide'; END IF;
 END LOOP;
 RETURN NEW;
END $$;

CREATE FUNCTION alcobre_dispatch_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE g record; r record;
BEGIN
 IF TG_TABLE_NAME='alcobre_dispatch_operations' THEN
  SELECT * INTO g FROM alcobre_dispatches WHERE id=NEW.dispatch_id AND context_id=NEW.context_id;
  IF g.status<>(CASE NEW.kind WHEN 'prepare' THEN 'prepared' WHEN 'confirm' THEN 'confirmed' ELSE 'cancelled' END) OR (SELECT count(*) FROM alcobre_dispatch_events WHERE operation_id=NEW.id AND context_id=NEW.context_id)<>jsonb_array_length(NEW.snapshot::jsonb->'events') OR NOT EXISTS(SELECT 1 FROM alcobre_dispatch_documents WHERE operation_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Operation, stock and document must finish atomically'; END IF;
 ELSE
  IF NEW.version>0 AND NOT EXISTS(SELECT 1 FROM alcobre_dispatch_revisions rv JOIN alcobre_dispatch_documents d ON d.revision_id=rv.id AND d.context_id=rv.context_id WHERE rv.dispatch_id=NEW.id AND rv.context_id=NEW.context_id AND rv.version=NEW.version) THEN RAISE EXCEPTION 'Current immutable revision and PDF required'; END IF;
  IF NEW.active_revision IS NOT NULL AND NOT EXISTS(SELECT 1 FROM alcobre_dispatch_revisions WHERE id=NEW.active_revision AND dispatch_id=NEW.id AND context_id=NEW.context_id AND version<=NEW.version) THEN RAISE EXCEPTION 'Active revision outside guide'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER alcobre_dispatch_header BEFORE INSERT OR UPDATE ON alcobre_dispatches FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_guard();
CREATE TRIGGER alcobre_dispatch_revision BEFORE INSERT ON alcobre_dispatch_revisions FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_guard();
CREATE TRIGGER alcobre_dispatch_operation BEFORE INSERT ON alcobre_dispatch_operations FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_guard();
CREATE TRIGGER alcobre_dispatch_event BEFORE INSERT ON alcobre_dispatch_events FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_guard();
CREATE TRIGGER alcobre_dispatch_document BEFORE INSERT ON alcobre_dispatch_documents FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_guard();
CREATE CONSTRAINT TRIGGER alcobre_dispatch_atomic AFTER INSERT ON alcobre_dispatch_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_final();
CREATE CONSTRAINT TRIGGER alcobre_dispatch_revision_complete AFTER INSERT OR UPDATE ON alcobre_dispatches DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION alcobre_dispatch_final();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['alcobre_dispatch_revisions','alcobre_dispatch_operations','alcobre_dispatch_events','alcobre_dispatch_documents'] LOOP
  EXECUTE format('CREATE TRIGGER %%I BEFORE UPDATE OR DELETE OR TRUNCATE ON %%I FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable()',t||'_immutable',t);
 END LOOP;
END $$;
CREATE TRIGGER alcobre_dispatch_no_delete BEFORE DELETE OR TRUNCATE ON alcobre_dispatches FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
