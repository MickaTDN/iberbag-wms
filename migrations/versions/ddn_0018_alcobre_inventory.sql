CREATE FUNCTION alcobre_current_placements() RETURNS TABLE(id integer,context_id integer,bobine_id integer,action_id integer,local varchar(120)) LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT CASE WHEN e.id IS NULL THEN p.id ELSE -e.id END,b.context_id,b.id,p.action_id,CASE WHEN e.id IS NULL THEN p.local ELSE e.local END
 FROM alcobre_bobines b LEFT JOIN alcobre_placements p ON p.bobine_id=b.id AND p.context_id=b.context_id
 LEFT JOIN LATERAL (SELECT x.id,x.local FROM alcobre_dispatch_events x WHERE x.bobine_id=b.id AND x.context_id=b.context_id AND x.control_operation_id IS NOT NULL ORDER BY x.id DESC LIMIT 1) e ON true
 WHERE CASE WHEN e.id IS NULL THEN p.local ELSE e.local END IS NOT NULL
$$;
CREATE FUNCTION alcobre_inventory_blocked(code text,ctx integer) RETURNS boolean LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM alcobre_inventory_checks c WHERE c.context_id=ctx AND c.result='unknown' AND c.local=code AND NOT EXISTS(SELECT 1 FROM alcobre_inventory_checks r WHERE r.resolves_id=c.id AND r.context_id=ctx))
$$;
CREATE FUNCTION alcobre_inventory_unit(unit_id integer,ctx integer) RETURNS jsonb LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('id',b.id,'number',b.number,'entry_id',b.entry_id,'event',alcobre_current_event(b.id),'state',alcobre_current_state(b.id),'placement_id',p.id,'local',p.local,'incident_version',coalesce((SELECT max(c.id) FROM alcobre_inventory_checks c WHERE c.context_id=ctx AND c.local=p.local AND c.result IN ('unknown','resolved')),0))
 FROM alcobre_bobines b LEFT JOIN alcobre_current_placements() p ON p.bobine_id=b.id AND p.context_id=ctx WHERE b.id=unit_id AND b.context_id=ctx
$$;
CREATE FUNCTION alcobre_inventory_pending(entry integer,ctx integer) RETURNS jsonb LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('entry_id',e.id,'quantity',e.quantity,'remaining',e.quantity-(SELECT count(*) FROM alcobre_bobines b WHERE b.entry_id=e.id AND b.context_id=ctx)) FROM alcobre_entries e WHERE e.context_id=ctx AND e.id=entry AND e.mode='pending' AND e.status='confirmed'
$$;
CREATE FUNCTION alcobre_inventory_location(wanted_code text,ctx integer) RETURNS jsonb LANGUAGE sql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
 SELECT jsonb_build_object('local',l.codigo,'active',l.active,'capacity',l.capacidade,'slot',s.id,'zone',z.id,'zone_active',z.active,'occupants',coalesce((SELECT jsonb_agg(alcobre_inventory_unit(p.bobine_id,ctx) ORDER BY p.bobine_id) FROM alcobre_current_placements() p WHERE p.context_id=ctx AND p.local=wanted_code AND alcobre_current_state(p.bobine_id) NOT IN ('shipped','missing')),'[]'::jsonb),'incident',alcobre_inventory_blocked(wanted_code,ctx),'incident_version',coalesce((SELECT max(c.id) FROM alcobre_inventory_checks c WHERE c.context_id=ctx AND c.local=wanted_code AND c.result IN ('unknown','resolved')),0))
 FROM locations l JOIN alcobre_slots s ON s.context_id=ctx AND s.local=l.codigo JOIN alcobre_zones z ON z.context_id=ctx AND z.id=s.zone_id WHERE l.context_id=ctx AND l.codigo=wanted_code
$$;
CREATE FUNCTION alcobre_inventory_scope(kind text,selected jsonb,ctx integer) RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE codes jsonb; units jsonb; pending jsonb;
BEGIN
 IF kind='complete' THEN
  SELECT coalesce(jsonb_agg(code ORDER BY code),'[]'::jsonb) INTO codes FROM (SELECT l.codigo AS code FROM locations l JOIN alcobre_slots s ON s.context_id=ctx AND s.local=l.codigo JOIN alcobre_zones z ON z.id=s.zone_id AND z.context_id=ctx WHERE l.context_id=ctx AND l.active AND z.active UNION SELECT p.local FROM alcobre_current_placements() p WHERE p.context_id=ctx AND alcobre_current_state(p.bobine_id)<>'shipped') a;
 ELSE
  IF kind<>'partial' OR coalesce(jsonb_typeof(selected),'null')<>'array' OR jsonb_array_length(selected) NOT BETWEEN 1 AND 500 THEN RAISE EXCEPTION 'Explicit partial location scope required'; END IF;
  SELECT jsonb_agg(x ORDER BY x) INTO codes FROM (SELECT DISTINCT x FROM jsonb_array_elements(selected) x) a;
  IF jsonb_array_length(codes)<>jsonb_array_length(selected) OR EXISTS(SELECT 1 FROM jsonb_array_elements(codes) x WHERE coalesce(alcobre_inventory_location(x #>> '{}',ctx)->>'active','false')<>'true' OR coalesce(alcobre_inventory_location(x #>> '{}',ctx)->>'zone_active','false')<>'true') THEN RAISE EXCEPTION 'Active mapped locations required'; END IF;
 END IF;
 SELECT coalesce(jsonb_agg(b.id ORDER BY b.id),'[]'::jsonb) INTO units FROM alcobre_bobines b LEFT JOIN alcobre_current_placements() p ON p.bobine_id=b.id AND p.context_id=ctx WHERE b.context_id=ctx AND alcobre_current_state(b.id)<>'shipped' AND (kind='complete' OR codes ? p.local);
 SELECT coalesce(jsonb_agg(e.id ORDER BY e.id),'[]'::jsonb) INTO pending FROM alcobre_entries e WHERE kind='complete' AND e.context_id=ctx AND e.mode='pending' AND e.status='confirmed' AND e.quantity>(SELECT count(*) FROM alcobre_bobines b WHERE b.entry_id=e.id AND b.context_id=ctx);
 IF jsonb_array_length(codes)>500 OR jsonb_array_length(units)>2000 OR jsonb_array_length(pending)>500 THEN RAISE EXCEPTION 'Scope exceeds safe document limits; use partial inventories'; END IF;
 RETURN jsonb_build_object('locations',codes,'units',units,'pending',pending);
END $$;
CREATE FUNCTION alcobre_inventory_summary(inv_id integer,ctx integer) RETURNS jsonb LANGUAGE plpgsql VOLATILE SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE inv record; c record; x jsonb; src jsonb; current_scope jsonb; rows jsonb='[]'; places jsonb='[]'; pending jsonb='[]'; incidents jsonb='[]'; errors jsonb='[]'; effects jsonb='[]'; decision text; action text; target text; dest jsonb; covered boolean;
BEGIN
 SELECT * INTO inv FROM alcobre_inventories WHERE id=inv_id AND context_id=ctx;
 IF inv.id IS NULL THEN RAISE EXCEPTION 'Inventory outside context'; END IF;
 BEGIN
  current_scope=alcobre_inventory_scope(inv.mode,inv.scope::jsonb->'locations',ctx);
 EXCEPTION WHEN raise_exception THEN
  current_scope=inv.scope::jsonb;errors=errors||jsonb_build_array('Catálogo/âmbito deixou de ser válido; rever configuração e criar novo inventário ou conferir novamente.');
 END;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(current_scope->'units') i WHERE NOT inv.scope::jsonb->'units' @> jsonb_build_array(i)) OR current_scope->'locations' IS DISTINCT FROM inv.scope::jsonb->'locations' OR current_scope->'pending' IS DISTINCT FROM inv.scope::jsonb->'pending' THEN errors=errors||jsonb_build_array('Âmbito alterado por entradas/catálogo/identificações; criar novo inventário.'); END IF;
 FOR x IN SELECT * FROM jsonb_array_elements(inv.scope::jsonb->'units') LOOP
  src=alcobre_inventory_unit((x #>> '{}')::integer,ctx);
  SELECT * INTO c FROM alcobre_inventory_checks WHERE inventory_id=inv.id AND context_id=ctx AND bobine_id=(x #>> '{}')::integer ORDER BY id DESC LIMIT 1;
  decision=coalesce(c.result,'unverified');action='none';target=src->>'local';
  IF decision='unverified' THEN errors=errors||jsonb_build_array('Bobine por conferir: '||(src->>'number'));
  ELSIF c.snapshot::jsonb IS DISTINCT FROM src THEN errors=errors||jsonb_build_array('Estado/reserva/localização mudou: '||(src->>'number'));
  ELSIF src->>'state' IN ('reserved','shipped') THEN
   IF NOT (decision='excluded' OR (src->>'state'='reserved' AND decision='found' AND c.local IS NOT DISTINCT FROM src->>'local')) THEN errors=errors||jsonb_build_array('Bobine reservada/expedida não ajustável: '||(src->>'number')); END IF;
  ELSIF decision='missing' THEN
   IF src->>'state' IN ('available','blocked') THEN action='missing'; END IF;
  ELSIF decision IN ('found','elsewhere') THEN
   target=c.local;
   IF src->>'state' IN ('blocked','missing') OR src->>'local' IS NULL THEN action='recover';
   ELSIF target IS DISTINCT FROM src->>'local' AND decision='elsewhere' THEN action='relocate';
   ELSIF target IS DISTINCT FROM src->>'local' THEN errors=errors||jsonb_build_array('Classificar explicitamente localização divergente: '||(src->>'number')); END IF;
   dest=alcobre_inventory_location(target,ctx);
   IF target IS NULL OR coalesce(dest->>'active','false')<>'true' OR coalesce(dest->>'zone_active','false')<>'true' OR dest->>'capacity'<>'1' OR alcobre_inventory_blocked(target,ctx) OR EXISTS(SELECT 1 FROM alcobre_current_placements() p WHERE p.context_id=ctx AND p.local=target AND p.bobine_id<>(src->>'id')::integer AND alcobre_current_state(p.bobine_id) NOT IN ('shipped','missing')) THEN errors=errors||jsonb_build_array('Destino sem prova/capacidade/configuração exclusiva: '||(src->>'number')); END IF;
  ELSE errors=errors||jsonb_build_array('Exclusão inválida ou classificação insuficiente: '||(src->>'number')); END IF;
  rows=rows||jsonb_build_array(jsonb_build_object('source',src,'check_id',c.id,'result',decision,'local',c.local,'evidence',c.evidence,'action',action,'recorded_quantity',CASE WHEN src->>'state' IN ('missing','shipped') THEN 0 ELSE 1 END,'physical_quantity',CASE WHEN decision IN ('found','elsewhere') THEN 1 WHEN decision='missing' THEN 0 ELSE NULL END,'difference',CASE WHEN decision IN ('found','elsewhere','missing') THEN (CASE WHEN decision='missing' THEN 0 ELSE 1 END)-(CASE WHEN src->>'state' IN ('missing','shipped') THEN 0 ELSE 1 END) ELSE NULL END));
  IF action<>'none' THEN effects=effects||jsonb_build_array(jsonb_build_object('bobine_id',(src->>'id')::integer,'previous_id',(src->>'event')::integer,'state',CASE WHEN action='missing' THEN 'missing' ELSE 'available' END,'local',target)); END IF;
 END LOOP;
 IF (SELECT count(*) FROM jsonb_array_elements(effects) e WHERE e->>'state'='available')<>(SELECT count(DISTINCT e->>'local') FROM jsonb_array_elements(effects) e WHERE e->>'state'='available') THEN errors=errors||jsonb_build_array('Dois ajustes não podem ocupar a mesma posição.'); END IF;
 FOR x IN SELECT * FROM jsonb_array_elements(inv.scope::jsonb->'locations') LOOP
  SELECT * INTO c FROM alcobre_inventory_checks WHERE inventory_id=inv.id AND context_id=ctx AND bobine_id IS NULL AND entry_id IS NULL AND number IS NULL AND local=x #>> '{}' ORDER BY id DESC LIMIT 1;
  covered=EXISTS(SELECT 1 FROM jsonb_array_elements(rows) r WHERE r->>'result'<>'unverified' AND ((r->'source'->>'local')=x #>> '{}' OR r->>'local'=x #>> '{}')) OR EXISTS(SELECT 1 FROM alcobre_inventory_checks q WHERE q.context_id=ctx AND q.inventory_id=inv.id AND q.local=x #>> '{}' AND q.result='unknown');
  IF NOT covered AND (c.id IS NULL OR c.result<>'empty' OR c.snapshot::jsonb IS DISTINCT FROM alcobre_inventory_location(x #>> '{}',ctx)) THEN errors=errors||jsonb_build_array('Posição ainda não conferida ou alterada: '||(x #>> '{}')); END IF;
  IF c.id IS NOT NULL AND c.result='empty' AND EXISTS(SELECT 1 FROM jsonb_array_elements(rows) r WHERE r->>'result' IN ('found','elsewhere') AND r->>'local'=x #>> '{}' AND (r->>'check_id')::integer<c.id) THEN errors=errors||jsonb_build_array('Conferências contraditórias de vazio/presença: '||(x #>> '{}')); END IF;
  places=places||jsonb_build_array(jsonb_build_object('local',x,'counted',covered OR (c.id IS NOT NULL AND c.result='empty' AND c.snapshot::jsonb=alcobre_inventory_location(x #>> '{}',ctx)),'check_id',c.id,'result',CASE WHEN covered THEN 'unit_conference' WHEN c.id IS NOT NULL THEN c.result ELSE 'unverified' END));
 END LOOP;
 FOR x IN SELECT * FROM jsonb_array_elements(inv.scope::jsonb->'pending') LOOP
  SELECT * INTO c FROM alcobre_inventory_checks WHERE context_id=ctx AND inventory_id=inv.id AND entry_id=(x #>> '{}')::integer ORDER BY id DESC LIMIT 1;
  src=alcobre_inventory_pending((x #>> '{}')::integer,ctx);
  IF c.id IS NULL OR c.result<>'excluded' OR c.snapshot::jsonb IS DISTINCT FROM src THEN errors=errors||jsonb_build_array('Receção sem identidade exige omissão explicitamente reconhecida, sem inventar bobines.'); END IF;
  pending=pending||jsonb_build_array(jsonb_build_object('source',src,'result',coalesce(c.result,'unverified'),'evidence',c.evidence));
 END LOOP;
 FOR c IN SELECT q.* FROM alcobre_inventory_checks q WHERE q.context_id=ctx AND q.inventory_id=inv.id AND q.result='unknown' ORDER BY q.id LOOP
  covered=EXISTS(SELECT 1 FROM alcobre_inventory_checks r WHERE r.context_id=ctx AND r.resolves_id=c.id);
  incidents=incidents||jsonb_build_array(jsonb_build_object('id',c.id,'number',c.number,'local',c.local,'evidence',c.evidence,'resolved',covered));
  IF NOT covered THEN errors=errors||jsonb_build_array('Ocorrência por investigar: '||c.number); END IF;
 END LOOP;
 RETURN jsonb_build_object('inventory_id',inv.id,'mode',inv.mode,'scope',inv.scope::jsonb,'rows',rows,'locations',places,'pending',pending,'incidents',incidents,'blocking',errors,'events',effects);
END $$;
CREATE FUNCTION alcobre_inventory_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE inv record; rv record; operation_record record; who record; src jsonb; raw jsonb; original record;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN alcobre_owners a ON a.client_id=c.client_id AND a.warehouse_id=c.warehouse_id JOIN clients cl ON cl.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.active AND cl.active AND w.active) THEN RAISE EXCEPTION 'Active Alcobre context required'; END IF;
 IF TG_TABLE_NAME='alcobre_inventories' THEN
  IF TG_OP='INSERT' THEN
   IF NEW.version<>1 OR NEW.status<>'draft' OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role IN ('admin','operador')) OR NEW.scope::jsonb IS DISTINCT FROM alcobre_inventory_scope(NEW.mode,NEW.scope::jsonb->'locations',NEW.context_id) OR NEW.opening::jsonb IS DISTINCT FROM jsonb_build_object('units',(SELECT coalesce(jsonb_agg(alcobre_inventory_unit((x #>> '{}')::integer,NEW.context_id) ORDER BY ord),'[]'::jsonb) FROM jsonb_array_elements(NEW.scope::jsonb->'units') WITH ORDINALITY ar(x,ord)),'pending',(SELECT coalesce(jsonb_agg(alcobre_inventory_pending((x #>> '{}')::integer,NEW.context_id) ORDER BY ord),'[]'::jsonb) FROM jsonb_array_elements(NEW.scope::jsonb->'pending') WITH ORDINALITY ar(x,ord))) THEN RAISE EXCEPTION 'Explicit scope and opening reference required'; END IF;
  ELSE
   IF OLD.status<>'draft' OR (to_jsonb(NEW)-ARRAY['version','status']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['version','status']) THEN RAISE EXCEPTION 'Inventory scope/history immutable'; END IF;
   IF NOT ((NEW.status='draft' AND NEW.version=OLD.version+1) OR (NEW.status='confirmed' AND NEW.version=OLD.version AND EXISTS(SELECT 1 FROM alcobre_inventory_operations WHERE inventory_id=NEW.id AND context_id=NEW.context_id))) THEN RAISE EXCEPTION 'Reviewed atomic confirmation required'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='alcobre_dispatch_events' THEN
  IF NEW.id<=coalesce(alcobre_current_event(NEW.bobine_id),0) THEN RAISE EXCEPTION 'Monotonic positive event identity required'; END IF;
  IF NEW.control_operation_id IS NULL THEN RETURN NEW; END IF;
  SELECT * INTO operation_record FROM alcobre_inventory_operations WHERE id=NEW.control_operation_id AND context_id=NEW.context_id;
  IF operation_record.id IS NULL OR NEW.operation_id IS NOT NULL OR NEW.previous_id IS DISTINCT FROM alcobre_current_event(NEW.bobine_id) OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(operation_record.snapshot::jsonb->'events') e WHERE e=jsonb_build_object('bobine_id',NEW.bobine_id,'previous_id',NEW.previous_id,'state',NEW.state,'local',NEW.local)) THEN RAISE EXCEPTION 'Only reviewed current correction events'; END IF;
  IF alcobre_current_state(NEW.bobine_id) IN ('reserved','shipped') OR NEW.state NOT IN ('available','missing') THEN RAISE EXCEPTION 'No correction of reservations/expeditions'; END IF;
  IF NEW.state='available' AND (coalesce(alcobre_inventory_location(NEW.local,NEW.context_id)->>'active','false')<>'true' OR coalesce(alcobre_inventory_location(NEW.local,NEW.context_id)->>'zone_active','false')<>'true' OR alcobre_inventory_location(NEW.local,NEW.context_id)->>'capacity'<>'1' OR alcobre_inventory_blocked(NEW.local,NEW.context_id) OR EXISTS(SELECT 1 FROM alcobre_current_placements() p WHERE p.context_id=NEW.context_id AND p.local=NEW.local AND p.bobine_id<>NEW.bobine_id AND alcobre_current_state(p.bobine_id) NOT IN ('shipped','missing'))) THEN RAISE EXCEPTION 'Destination changed before correction event'; END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='alcobre_inventory_documents' THEN
  IF NEW.digest<>encode(sha256(NEW.pdf),'hex') OR octet_length(NEW.pdf) NOT BETWEEN 1 AND 4194304 THEN RAISE EXCEPTION 'Archived PDF integrity required'; END IF;
  IF NEW.review_id IS NOT NULL THEN SELECT i.* INTO inv FROM alcobre_inventory_reviews r JOIN alcobre_inventories i ON i.id=r.inventory_id AND i.context_id=r.context_id WHERE r.id=NEW.review_id AND r.context_id=NEW.context_id;
  ELSE SELECT i.* INTO inv FROM alcobre_inventory_operations o JOIN alcobre_inventories i ON i.id=o.inventory_id AND i.context_id=o.context_id WHERE o.id=NEW.operation_id AND o.context_id=NEW.context_id; END IF;
 ELSE SELECT * INTO inv FROM alcobre_inventories WHERE id=NEW.inventory_id AND context_id=NEW.context_id; END IF;
 SELECT * INTO who FROM users WHERE username=NEW.actor AND active AND role IN ('admin','operador');
 IF inv.id IS NULL OR who.id IS NULL OR (who.id<>inv.created_by AND who.role<>'admin') THEN RAISE EXCEPTION 'Authorized inventory actor required'; END IF;
 IF TG_TABLE_NAME='alcobre_inventory_documents' THEN RETURN NEW; END IF;
 IF inv.status<>'draft' THEN RAISE EXCEPTION 'Confirmed inventory immutable'; END IF;
 IF TG_TABLE_NAME='alcobre_inventory_checks' THEN
  IF EXISTS(SELECT 1 FROM alcobre_inventory_reviews WHERE inventory_id=inv.id AND context_id=NEW.context_id AND version=inv.version) THEN RAISE EXCEPTION 'Advance inventory version before adding a conference'; END IF;
  IF length(NEW.evidence) NOT BETWEEN 1 AND 2000 OR btrim(NEW.evidence)<>NEW.evidence THEN RAISE EXCEPTION 'Explicit evidence required'; END IF;
  IF NEW.bobine_id IS NOT NULL THEN
   src=alcobre_inventory_unit(NEW.bobine_id,NEW.context_id);
   IF src IS NULL OR NOT inv.scope::jsonb->'units' @> jsonb_build_array(NEW.bobine_id) OR NEW.number IS DISTINCT FROM src->>'number' OR NEW.snapshot::jsonb IS DISTINCT FROM src OR NEW.entry_id IS NOT NULL OR NEW.resolves_id IS NOT NULL OR NEW.result NOT IN ('found','missing','elsewhere','unverified','excluded') OR (NEW.result IN ('found','elsewhere') AND NEW.local IS NULL) OR (NEW.result IN ('missing','unverified','excluded') AND NEW.local IS NOT NULL) THEN RAISE EXCEPTION 'Exact scoped bobine and explicit conference required'; END IF;
  ELSIF NEW.entry_id IS NOT NULL THEN
   IF NEW.number IS NOT NULL OR NEW.local IS NOT NULL OR NEW.result<>'excluded' OR NEW.resolves_id IS NOT NULL OR NOT inv.scope::jsonb->'pending' @> jsonb_build_array(NEW.entry_id) OR NEW.snapshot::jsonb IS DISTINCT FROM alcobre_inventory_pending(NEW.entry_id,NEW.context_id) THEN RAISE EXCEPTION 'Pending quantities cannot become invented identities'; END IF;
  ELSIF NEW.result='resolved' THEN
   SELECT * INTO original FROM alcobre_inventory_checks WHERE id=NEW.resolves_id AND inventory_id=inv.id AND context_id=NEW.context_id AND result='unknown';
   IF who.role<>'admin' OR original.id IS NULL OR NEW.number IS DISTINCT FROM original.number OR NEW.local IS DISTINCT FROM original.local OR NEW.snapshot::jsonb IS DISTINCT FROM jsonb_build_object('original',original.id) THEN RAISE EXCEPTION 'Administrative evidenced incident decision required'; END IF;
  ELSIF NEW.number IS NOT NULL THEN
   IF NEW.result<>'unknown' OR NEW.resolves_id IS NOT NULL OR length(NEW.number) NOT BETWEEN 1 AND 120 OR NEW.number<>btrim(NEW.number) OR (NEW.local IS NOT NULL AND (NOT inv.scope::jsonb->'locations' ? NEW.local OR alcobre_inventory_location(NEW.local,NEW.context_id) IS NULL)) OR NEW.snapshot::jsonb<>'{}'::jsonb THEN RAISE EXCEPTION 'Unknown identity creates investigation only'; END IF;
  ELSE
   IF NEW.result NOT IN ('empty','unverified') OR NEW.local IS NULL OR NOT inv.scope::jsonb->'locations' ? NEW.local OR NEW.resolves_id IS NOT NULL OR NEW.snapshot::jsonb IS DISTINCT FROM alcobre_inventory_location(NEW.local,NEW.context_id) OR (NEW.result='empty' AND (jsonb_array_length(NEW.snapshot::jsonb->'occupants')<>0 OR (NEW.snapshot::jsonb->>'incident')::boolean)) THEN RAISE EXCEPTION 'Empty must be counted explicitly without hiding registered bobines'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF TG_TABLE_NAME='alcobre_inventory_reviews' THEN
  IF NEW.version<>inv.version OR NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') OR btrim(NEW.reason)='' OR NEW.payload::jsonb IS DISTINCT FROM alcobre_inventory_summary(inv.id,NEW.context_id) OR (NEW.kind='approve' AND (who.role<>'admin' OR jsonb_array_length(NEW.payload::jsonb->'blocking')<>0)) THEN RAISE EXCEPTION 'Current complete administrative review required'; END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO rv FROM alcobre_inventory_reviews WHERE id=NEW.review_id AND inventory_id=inv.id AND context_id=NEW.context_id AND kind='approve';
 raw=alcobre_inventory_summary(inv.id,NEW.context_id);
 IF who.role<>'admin' OR rv.id IS NULL OR rv.version<>inv.version OR NEW.payload::jsonb IS DISTINCT FROM jsonb_build_object('review',rv.id,'digest',rv.digest) OR rv.payload::jsonb IS DISTINCT FROM raw OR jsonb_array_length(raw->'blocking')<>0 OR NEW.snapshot::jsonb IS DISTINCT FROM raw THEN RAISE EXCEPTION 'Atomic current approved correction required'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION alcobre_inventory_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF TG_TABLE_NAME='alcobre_inventory_operations' THEN
  IF NOT EXISTS(SELECT 1 FROM alcobre_inventories WHERE id=NEW.inventory_id AND context_id=NEW.context_id AND status='confirmed') OR (SELECT count(*) FROM alcobre_dispatch_events WHERE control_operation_id=NEW.id AND context_id=NEW.context_id)<>jsonb_array_length(NEW.snapshot::jsonb->'events') OR NOT EXISTS(SELECT 1 FROM alcobre_inventory_documents WHERE operation_id=NEW.id AND context_id=NEW.context_id) THEN RAISE EXCEPTION 'Corrections, inventory and PDF must commit together'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM alcobre_inventory_reviews r JOIN alcobre_inventory_documents d ON d.review_id=r.id AND d.context_id=r.context_id WHERE r.context_id=NEW.context_id AND r.inventory_id=NEW.id AND r.version=NEW.version) THEN RAISE EXCEPTION 'Every inventory version requires archived conference'; END IF;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER alcobre_inventory_header BEFORE INSERT OR UPDATE ON alcobre_inventories FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_guard();
CREATE TRIGGER alcobre_inventory_check BEFORE INSERT ON alcobre_inventory_checks FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_guard();
CREATE TRIGGER alcobre_inventory_review BEFORE INSERT ON alcobre_inventory_reviews FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_guard();
CREATE TRIGGER alcobre_inventory_operation BEFORE INSERT ON alcobre_inventory_operations FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_guard();
CREATE TRIGGER alcobre_inventory_document BEFORE INSERT ON alcobre_inventory_documents FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_guard();
CREATE TRIGGER alcobre_inventory_event BEFORE INSERT ON alcobre_dispatch_events FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_guard();
CREATE CONSTRAINT TRIGGER alcobre_inventory_atomic AFTER INSERT ON alcobre_inventory_operations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_final();
CREATE CONSTRAINT TRIGGER alcobre_inventory_complete AFTER INSERT OR UPDATE ON alcobre_inventories DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION alcobre_inventory_final();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['alcobre_inventory_checks','alcobre_inventory_reviews','alcobre_inventory_operations','alcobre_inventory_documents'] LOOP
  EXECUTE format('CREATE TRIGGER %%I BEFORE UPDATE OR DELETE OR TRUNCATE ON %%I FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable()',t||'_immutable',t);
 END LOOP;
END $$;
CREATE TRIGGER alcobre_inventory_no_delete BEFORE DELETE OR TRUNCATE ON alcobre_inventories FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
