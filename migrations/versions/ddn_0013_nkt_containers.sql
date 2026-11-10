CREATE FUNCTION nkt_receiving_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
DECLARE parent record;rev record;entryrow record;loadrow record;owner integer;
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT c.client_id INTO owner FROM storage_contexts c JOIN nkt_owners n ON n.client_id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id JOIN clients a ON a.id=c.client_id WHERE c.id=NEW.context_id AND c.active AND w.active AND a.active AND w.name='Escapães';
 IF owner IS NULL THEN RAISE EXCEPTION 'NKT Escapães required'; END IF;
 IF TG_TABLE_NAME='nkt_receivings' THEN
  IF NEW.client_id<>owner OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND role IN ('admin','operador') AND active) THEN RAISE EXCEPTION 'Invalid receiving owner/actor'; END IF;
  IF NEW.source_digest IS NOT NULL AND (NEW.source_digest !~ '^[a-f0-9]{64}$' OR (NEW.report::jsonb->>'sha256') IS DISTINCT FROM NEW.source_digest OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND role='admin')) THEN RAISE EXCEPTION 'Administrative file provenance required'; END IF;
  IF TG_OP='INSERT' AND (NEW.status<>'draft' OR NEW.version<>0) THEN RAISE EXCEPTION 'Start with preview'; END IF;
  IF TG_OP='UPDATE' THEN
   IF OLD.status='confirmed' OR ROW(NEW.context_id,NEW.client_id,NEW.created_by,NEW.created_at,NEW.request_key,NEW.source_name,NEW.source_digest,NEW.report) IS DISTINCT FROM ROW(OLD.context_id,OLD.client_id,OLD.created_by,OLD.created_at,OLD.request_key,OLD.source_name,OLD.source_digest,OLD.report) THEN RAISE EXCEPTION 'Immutable source/confirmed receiving'; END IF;
   IF NEW.status='draft' AND NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'Revision must advance'; END IF;
   IF NEW.status='confirmed' THEN
    SELECT * INTO rev FROM nkt_receiving_reviews WHERE receiving_id=NEW.id AND context_id=NEW.context_id AND version=NEW.version;
    IF rev.id IS NULL OR NEW.version<>OLD.version OR (SELECT count(*) FROM nkt_containers WHERE receiving_id=NEW.id AND context_id=NEW.context_id)<>jsonb_array_length(rev.payload::jsonb) THEN RAISE EXCEPTION 'Incomplete receiving'; END IF;
   END IF;
  END IF;
 ELSE
  SELECT * INTO parent FROM nkt_receivings WHERE id=NEW.receiving_id AND context_id=NEW.context_id AND status='draft';
  IF parent.id IS NULL OR NOT EXISTS(SELECT 1 FROM users WHERE id=parent.created_by AND username=NEW.actor AND active) THEN RAISE EXCEPTION 'Draft author required'; END IF;
  IF TG_TABLE_NAME='nkt_receiving_reviews' THEN
   IF NEW.version<>parent.version OR NEW.digest<>encode(sha256(convert_to(NEW.payload,'UTF8')),'hex') OR btrim(NEW.reason)='' OR jsonb_typeof(NEW.payload::jsonb) IS DISTINCT FROM 'array' OR jsonb_array_length(NEW.payload::jsonb) NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION 'Invalid review'; END IF;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.payload::jsonb) g WHERE coalesce(btrim(g->>'container'),'')='' OR coalesce(g->>'received_on','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' OR jsonb_typeof(g->'items') IS DISTINCT FROM 'array') THEN RAISE EXCEPTION 'Explicit container/date/items required'; END IF;
   PERFORM (g->>'received_on')::date FROM jsonb_array_elements(NEW.payload::jsonb) g;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.payload::jsonb) g WHERE jsonb_array_length(g->'items') NOT BETWEEN 1 AND 60 OR EXISTS(SELECT 1 FROM jsonb_array_elements(g->'items') i WHERE jsonb_typeof(i->'peg') IS DISTINCT FROM 'string' OR coalesce(btrim(i->>'peg'),'')='' OR coalesce(btrim(i->>'material'),'')='')) THEN RAISE EXCEPTION 'Physical PEG and material required'; END IF;

  ELSE
   SELECT * INTO rev FROM nkt_receiving_reviews WHERE id=NEW.review_id AND receiving_id=parent.id AND context_id=NEW.context_id AND version=parent.version;
   IF NEW.client_id<>owner OR rev.id IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(rev.payload::jsonb) g WHERE g=NEW.payload::jsonb AND g->>'container'=NEW.name) THEN RAISE EXCEPTION 'Unreviewed container'; END IF;
   SELECT * INTO entryrow FROM nkt_entries WHERE id=NEW.entry_id AND context_id=NEW.context_id AND status='confirmed';
   SELECT * INTO loadrow FROM cargas WHERE id=NEW.carga_id AND context_id=NEW.context_id;
   IF entryrow.id IS NULL OR loadrow.id IS NULL OR entryrow.created_by<>parent.created_by OR (entryrow.payload::jsonb->>'received_on') IS DISTINCT FROM (NEW.payload::jsonb->>'received_on') OR loadrow.entrada::date IS DISTINCT FROM (NEW.payload::jsonb->>'received_on')::date OR loadrow.funcionario IS DISTINCT FROM NEW.actor THEN RAISE EXCEPTION 'Container entry/date mismatch'; END IF;
   IF jsonb_array_length(NEW.payload::jsonb->'items')<>(SELECT count(*) FROM nkt_pallets WHERE entry_id=NEW.entry_id AND context_id=NEW.context_id) OR EXISTS(SELECT 1 FROM nkt_pallets p WHERE p.entry_id=NEW.entry_id AND p.context_id=NEW.context_id AND (p.local<>'Chão' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.payload::jsonb->'items') x WHERE x->>'peg'=p.peg AND coalesce(btrim(x->>'material'),'')<>''))) THEN RAISE EXCEPTION 'Container physical/material mismatch'; END IF;
   IF (NEW.payload::jsonb->>'received_on')::date>=DATE '2027-01-01' THEN
    IF NEW.year IS DISTINCT FROM extract(year FROM (NEW.payload::jsonb->>'received_on')::date)::integer OR NEW.number IS NULL OR loadrow.codigo IS DISTINCT FROM right(NEW.year::text,2)||'-'||lpad(NEW.number::text,greatest(3,length(NEW.number::text)),'0') OR EXISTS(SELECT 1 FROM load_numbers WHERE client_id=owner AND year=NEW.year AND number=NEW.number) THEN RAISE EXCEPTION 'Invalid annual number'; END IF;
   ELSIF NEW.year IS NOT NULL OR NEW.number IS NOT NULL THEN RAISE EXCEPTION 'No invented legacy numbering'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION nkt_receiving_final() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM nkt_receivings WHERE id=NEW.receiving_id AND context_id=NEW.context_id AND status='confirmed') THEN RAISE EXCEPTION 'Container receiving must confirm atomically'; END IF; RETURN NULL;
END $$;
CREATE FUNCTION nkt_carga_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF EXISTS(SELECT 1 FROM nkt_containers WHERE carga_id=OLD.id AND context_id=OLD.context_id) THEN RAISE EXCEPTION 'NKT container load immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW;
END $$;
CREATE TRIGGER nkt_receiving_guard BEFORE INSERT OR UPDATE ON nkt_receivings FOR EACH ROW EXECUTE FUNCTION nkt_receiving_guard();
CREATE TRIGGER nkt_receiving_confirmed BEFORE UPDATE OR DELETE ON nkt_receivings FOR EACH ROW EXECUTE FUNCTION wms_immutable_confirmation();
CREATE TRIGGER nkt_receiving_review_guard BEFORE INSERT ON nkt_receiving_reviews FOR EACH ROW EXECUTE FUNCTION nkt_receiving_guard();
CREATE TRIGGER nkt_container_guard BEFORE INSERT ON nkt_containers FOR EACH ROW EXECUTE FUNCTION nkt_receiving_guard();
CREATE TRIGGER nkt_receiving_review_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_receiving_reviews FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE TRIGGER nkt_container_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON nkt_containers FOR EACH STATEMENT EXECUTE FUNCTION valmet_immutable();
CREATE CONSTRAINT TRIGGER nkt_receiving_final AFTER INSERT ON nkt_containers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nkt_receiving_final();
CREATE TRIGGER nkt_carga_immutable BEFORE UPDATE OR DELETE ON cargas FOR EACH ROW EXECUTE FUNCTION nkt_carga_immutable();
CREATE FUNCTION nkt_shared_number_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=__WMS_SCHEMA__,pg_catalog,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(72834001);
 IF EXISTS(SELECT 1 FROM nkt_containers WHERE client_id=NEW.client_id AND year=NEW.year AND number=NEW.number) THEN RAISE EXCEPTION 'Number already issued to NKT container'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER nkt_shared_number BEFORE INSERT ON load_numbers FOR EACH ROW EXECUTE FUNCTION nkt_shared_number_guard();
