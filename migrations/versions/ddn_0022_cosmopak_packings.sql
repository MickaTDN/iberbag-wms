DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM locations l JOIN storage_contexts c ON c.id=l.context_id WHERE c.warehouse_id=1 AND lower(btrim(l.codigo))='zona de preparação')
 OR EXISTS(SELECT 1 FROM stock s JOIN storage_contexts c ON c.id=s.context_id WHERE c.warehouse_id=1 AND lower(btrim(s.local))='zona de preparação')
 OR EXISTS(SELECT 1 FROM physical_location_claims WHERE warehouse_id=1 AND code='zona de preparação') THEN
 RAISE EXCEPTION 'Reconcile historical staging label before activating logical area';END IF;
END $$;
INSERT INTO physical_staging_areas(id,warehouse_id,label) VALUES(1,1,'Zona de preparação');
SELECT setval(pg_get_serial_sequence('physical_staging_areas','id'),1,true);
CREATE FUNCTION ddn_packing_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR current_user<>pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) OR pg_trigger_depth()<2 THEN
 RAISE EXCEPTION 'Packing projection and output books are protected' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE FUNCTION ddn_packing_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN clients o ON o.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.client_id=3 AND c.warehouse_id=1 AND c.active AND o.active AND w.active)
 OR NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.created_by AND active AND role='admin') OR octet_length(NEW.source_excel)>20971520
 OR NEW.source_sha256<>encode(sha256(NEW.source_excel),'hex') OR NEW.report::jsonb->>'sha256' IS DISTINCT FROM NEW.source_sha256 OR NEW.report::jsonb->>'sheet' IS DISTINCT FROM 'Folha2' THEN
 RAISE EXCEPTION 'Verified Cosmopak PACKING source required' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE TRIGGER packing_source_guard BEFORE INSERT ON cosmo_packings FOR EACH ROW EXECUTE FUNCTION ddn_packing_source();
CREATE FUNCTION ddn_packing_initialize() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
 IF TG_RELID<>'cosmo_packings'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted packing initializer' USING ERRCODE='23514';END IF;
 INSERT INTO cosmo_packing_states(context_id,packing_id,stage,version,loading_started) VALUES(NEW.context_id,NEW.id,'draft',0,false);RETURN NEW;
END $$;
CREATE TRIGGER packing_initialize AFTER INSERT ON cosmo_packings FOR EACH ROW EXECUTE FUNCTION ddn_packing_initialize();
CREATE FUNCTION ddn_packing_snapshot(ident integer,ctx integer) RETURNS jsonb LANGUAGE sql AS $$
 SELECT jsonb_build_object('packing',to_jsonb(h),'items',coalesce((SELECT jsonb_agg(jsonb_build_object(
 'pick',to_jsonb(k),'line',to_jsonb(l),'pallet',to_jsonb(p),'material',to_jsonb(b),'physical',to_jsonb(z),'stock',to_jsonb(st),'prepared_user',(SELECT username FROM users WHERE id=k.prepared_by),'loaded_user',(SELECT username FROM users WHERE id=k.loaded_by)) ORDER BY l.source_row,l.ordinal)
 FROM cosmo_packing_picks k JOIN cosmo_packing_lines l ON l.id=k.line_id AND l.context_id=k.context_id
 LEFT JOIN cosmo_pallets p ON p.id=k.pallet_id AND p.context_id=k.context_id LEFT JOIN cosmo_states b ON b.pallet_id=p.id AND b.context_id=p.context_id
 LEFT JOIN stock st ON st.id=b.stock_id AND st.context_id=b.context_id
 LEFT JOIN physical_staging_positions z ON z.unit_kind='cosmopak' AND z.unit_id=p.id AND z.context_id=p.context_id
 WHERE k.packing_id=ident AND k.context_id=ctx),'[]'::jsonb)) FROM cosmo_packing_states h WHERE h.packing_id=ident AND h.context_id=ctx
$$;
CREATE FUNCTION ddn_staging_apply() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE p physical_staging_positions;n physical_staging_positions;a cosmo_packing_actions;m cosmo_packing_moves;
BEGIN
 IF TG_RELID<>'physical_staging_events'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted common staging event' USING ERRCODE='23514';END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO a FROM cosmo_packing_actions WHERE id=NEW.source_id AND context_id=NEW.context_id;
 SELECT * INTO m FROM cosmo_packing_moves WHERE action_id=a.id AND pallet_id=NEW.unit_id AND context_id=NEW.context_id;
 IF a.id IS NULL OR m.id IS NULL OR NEW.source_kind<>'cosmo_packing' OR NEW.unit_kind<>'cosmopak' OR NEW.group_kind<>'cosmo_packing'
 OR NOT EXISTS(SELECT 1 FROM physical_staging_areas WHERE id=NEW.area_id AND warehouse_id=1 AND label='Zona de preparação')
 OR (NEW.phase='staged' AND a.kind NOT IN ('prepare','return_stage')) OR(NEW.phase='loading' AND a.kind<>'load')
 OR (NEW.phase='rack' AND a.kind<>'return_rack') OR(NEW.phase='shipped' AND a.kind<>'approve')
 OR NEW.group_id<>a.packing_id
 OR (NEW.phase<>'shipped' AND (a.payload::jsonb->>'physical') IS DISTINCT FROM 'true') THEN RAISE EXCEPTION 'Proven typed staging movement required' USING ERRCODE='23514';END IF;
 SELECT * INTO p FROM physical_staging_positions WHERE context_id=NEW.context_id AND unit_kind=NEW.unit_kind AND unit_id=NEW.unit_id FOR UPDATE;
 NEW.before_state:=coalesce(to_jsonb(p),'null'::jsonb)::text;
 IF p.id IS NULL THEN
 INSERT INTO physical_staging_positions(context_id,area_id,unit_kind,unit_id,group_kind,group_id,phase,version) VALUES(NEW.context_id,NEW.area_id,NEW.unit_kind,NEW.unit_id,NEW.group_kind,NEW.group_id,NEW.phase,1) RETURNING * INTO n;
 ELSE
 UPDATE physical_staging_positions SET group_kind=NEW.group_kind,group_id=NEW.group_id,phase=NEW.phase,version=version+1 WHERE id=p.id RETURNING * INTO n;
 END IF;
 NEW.after_state:=to_jsonb(n)::text;RETURN NEW;
END $$;
CREATE TRIGGER staging_event_apply BEFORE INSERT ON physical_staging_events FOR EACH ROW EXECUTE FUNCTION ddn_staging_apply();

-- Ordinary helpers inherit the trusted command trigger's role. They cannot
-- elevate a runtime caller, even if a restore resets PUBLIC EXECUTE defaults.
CREATE FUNCTION ddn_packing_move(a cosmo_packing_actions,pallet integer,destination text,groupid integer,phase text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE b cosmo_states;n cosmo_states;p cosmo_pallets;old stock;target stock;master physical_locations;pos physical_staging_positions;move_id integer;
BEGIN
 SELECT * INTO p FROM cosmo_pallets WHERE id=pallet AND context_id=a.context_id;
 SELECT * INTO b FROM cosmo_states WHERE pallet_id=p.id AND context_id=a.context_id FOR UPDATE;
 SELECT * INTO old FROM stock WHERE id=b.stock_id AND context_id=a.context_id FOR UPDATE;
 IF b.status<>'approved' OR old.quantidade<1 THEN RAISE EXCEPTION 'Available approved physical pallet required' USING ERRCODE='23514';END IF;
 SELECT * INTO pos FROM physical_staging_positions WHERE unit_kind='cosmopak' AND unit_id=p.id AND context_id=a.context_id;
 n:=b;n.version:=b.version+1;
 IF destination<>b.local THEN
   IF destination<>'Zona de preparação' THEN
    SELECT x.* INTO master FROM physical_locations x JOIN physical_location_access y ON y.physical_id=x.id WHERE y.context_id=a.context_id AND x.label=destination;
    IF master.id IS NULL THEN RAISE EXCEPTION 'Configured shared rack required' USING ERRCODE='23514';END IF;
    INSERT INTO physical_location_events(context_id,physical_id,kind,ref_id,actor_id,reason,before_state,after_state,created_at) VALUES(a.context_id,master.id,'cosmo_return_hold',a.id,a.actor_id,a.reason,'','',a.created_at);
   END IF;
   SELECT * INTO target FROM stock WHERE context_id=a.context_id AND artigo=old.artigo AND lote=old.lote AND carga=old.carga AND local=destination AND NOT stacked FOR UPDATE;
   IF target.id IS NULL THEN
    INSERT INTO stock(context_id,artigo,lote,carga,local,quantidade,stacked) VALUES(a.context_id,old.artigo,old.lote,old.carga,destination,1,false) RETURNING * INTO target;
   ELSE UPDATE stock SET quantidade=quantidade+1 WHERE id=target.id RETURNING * INTO target;END IF;
   UPDATE stock SET quantidade=quantidade-1 WHERE id=old.id;
   n.local:=destination;n.stock_id:=target.id;
 END IF;
 IF phase='shipped' THEN
  UPDATE stock SET quantidade=quantidade-1 WHERE id=n.stock_id;n.status:='shipped';
 END IF;
 UPDATE cosmo_states SET local=n.local,stock_id=n.stock_id,status=n.status,version=n.version WHERE id=b.id;
 INSERT INTO cosmo_packing_moves(context_id,action_id,pallet_id,before_state,after_state) VALUES(a.context_id,a.id,p.id,to_jsonb(b)::text,to_jsonb(n)::text) RETURNING id INTO move_id;
 IF destination<>b.local THEN
   IF b.local<>'Zona de preparação' THEN
    INSERT INTO physical_location_events(context_id,physical_id,kind,ref_id,stock_id,actor_id,reason,before_state,after_state,created_at)
      SELECT a.context_id,id,'cosmo_stage_out',a.id,b.stock_id,a.actor_id,a.reason,'','',a.created_at FROM physical_locations WHERE warehouse_id=1 AND label=b.local;
   ELSE
    INSERT INTO physical_location_events(context_id,physical_id,kind,ref_id,stock_id,actor_id,reason,before_state,after_state,created_at) VALUES(a.context_id,master.id,'cosmo_return_commit',a.id,n.stock_id,a.actor_id,a.reason,'','',a.created_at);
   END IF;
 END IF;
 INSERT INTO physical_staging_events(context_id,area_id,unit_kind,unit_id,group_kind,group_id,phase,source_kind,source_id,before_state,after_state)
 VALUES(a.context_id,1,'cosmopak',p.id,'cosmo_packing',groupid,phase,'cosmo_packing',a.id,'','');
END $$;
CREATE FUNCTION ddn_packing_command() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE h cosmo_packing_states;k cosmo_packing_picks;l cosmo_packing_lines;b cosmo_states;p cosmo_pallets;u users;
 z physical_staging_positions;src cosmo_packings;x jsonb;j jsonb;before jsonb;scope_pick boolean;selected integer;lineid integer;slot cosmo_packing_picks;source_row jsonb;owner_key text;qty numeric;boxqty numeric;expected_count integer;total_count integer;field text;col text;cellref text;
BEGIN
 IF TG_RELID<>'cosmo_packing_actions'::regclass OR TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Untrusted PACKING command' USING ERRCODE='23514';END IF;
 PERFORM pg_advisory_xact_lock(72834001);
 SELECT * INTO u FROM users WHERE id=NEW.actor_id AND active AND role IN('admin','operador');
 SELECT * INTO h FROM cosmo_packing_states WHERE packing_id=NEW.packing_id AND context_id=NEW.context_id FOR UPDATE;
 SELECT * INTO src FROM cosmo_packings WHERE id=NEW.packing_id AND context_id=NEW.context_id;
 IF u.id IS NULL OR h.id IS NULL OR length(btrim(NEW.reason)) NOT BETWEEN 1 AND 2000 OR NOT EXISTS(SELECT 1 FROM storage_contexts c JOIN clients o ON o.id=c.client_id JOIN warehouses w ON w.id=c.warehouse_id WHERE c.id=NEW.context_id AND c.client_id=3 AND c.warehouse_id=1 AND c.active AND o.active AND w.active) THEN RAISE EXCEPTION 'Authorized Cosmopak command required' USING ERRCODE='23514';END IF;
 j:=NEW.payload::jsonb;
 scope_pick:=NEW.kind IN ('substitute','prepare','unprepare','exception','load','unload','return_stage');
 IF scope_pick THEN
  SELECT * INTO k FROM cosmo_packing_picks WHERE id=NEW.pick_id AND packing_id=h.packing_id AND context_id=NEW.context_id FOR UPDATE;
  SELECT * INTO l FROM cosmo_packing_lines WHERE id=k.line_id AND context_id=NEW.context_id;
  IF k.id IS NULL OR NOT k.active OR k.version<>NEW.expected_version THEN RAISE EXCEPTION 'Stale or inactive PACKING pallet' USING ERRCODE='23514';END IF;
  SELECT * INTO b FROM cosmo_states WHERE pallet_id=k.pallet_id AND context_id=NEW.context_id FOR UPDATE;
  SELECT * INTO p FROM cosmo_pallets WHERE id=k.pallet_id AND context_id=NEW.context_id;
  SELECT * INTO z FROM physical_staging_positions WHERE unit_kind='cosmopak' AND unit_id=k.pallet_id AND context_id=NEW.context_id;
  before:=to_jsonb(k);
 ELSE
  IF NEW.kind<>'return_rack' AND h.version<>NEW.expected_version THEN RAISE EXCEPTION 'Stale PACKING stage' USING ERRCODE='23514';END IF;
  before:=to_jsonb(h);
 END IF;
 IF NEW.kind IN ('map','confirm','substitute','reopen_prepare','reopen_load','review_dispatch','approve') AND u.role<>'admin' THEN RAISE EXCEPTION 'Office administrator required' USING ERRCODE='23514';END IF;
 IF NEW.kind='map' THEN
  IF h.stage NOT IN ('draft','reviewed') OR j->>'reference' IS NULL OR length(j->>'reference') NOT BETWEEN 1 AND 120 OR (h.reference IS NOT NULL AND h.reference<>j->>'reference')
   OR jsonb_typeof(j->'lines') IS DISTINCT FROM 'array' OR coalesce(jsonb_array_length(j->'lines'),0) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Explicit immutable official PACKING reference and mapping required' USING ERRCODE='23514';END IF;
  -- Reconstruct the complete mapped DDN row set, including multiplicity. A
  -- command cannot omit source rows, duplicate ordinals or multiply palletes.
  IF jsonb_typeof(j->'mapping') IS DISTINCT FROM 'object' OR coalesce(j->'mapping'->>'first_row','') !~ '^[0-9]{1,4}$'
   OR (j->'mapping'->>'first_row')::integer NOT BETWEEN 1 AND 1001 THEN RAISE EXCEPTION 'Explicit bounded mapping required' USING ERRCODE='23514';END IF;
  FOREACH field IN ARRAY ARRAY['owner','article','lot','boxes'] LOOP
   IF coalesce(j->'mapping'->>field,'') !~ '^[A-Z]$' THEN RAISE EXCEPTION 'Explicit source column required' USING ERRCODE='23514';END IF;
  END LOOP;
  IF (SELECT count(DISTINCT value) FROM jsonb_each_text(j->'mapping') WHERE key IN('owner','article','lot','boxes','pallets') AND value<>'')
   <>(CASE WHEN coalesce(j->'mapping'->>'pallets','')='' THEN 4 ELSE 5 END)
   OR (coalesce(j->'mapping'->>'pallets','')<>'' AND (j->'mapping'->>'pallets') !~ '^[A-Z]$')
   OR (coalesce(j->'mapping'->>'pallets','')='' AND j->'mapping'->>'one_row_one_pallet' IS DISTINCT FROM 'true') THEN RAISE EXCEPTION 'Distinct columns and explicit physical quantity required' USING ERRCODE='23514';END IF;
  total_count:=0;
  FOR source_row IN SELECT value FROM jsonb_array_elements(src.report::jsonb->'rows') WHERE (value->>'row')::integer>=(j->'mapping'->>'first_row')::integer LOOP
   IF NOT EXISTS(SELECT 1 FROM jsonb_each_text(j->'mapping') cols WHERE cols.key IN('owner','article','lot','boxes') AND coalesce(source_row->'cells'->>(ascii(cols.value)-65),'')<>'') THEN CONTINUE;END IF;
   owner_key:=regexp_replace(lower(source_row->'cells'->>(ascii(j->'mapping'->>'owner')-65)),'[^a-z0-9]','','g');
   IF owner_key='mecalux' THEN CONTINUE;END IF;
   IF coalesce(owner_key,'') NOT IN('davidneto','ddn','davidnetoddn') THEN RAISE EXCEPTION 'Ambiguous owner cannot be omitted' USING ERRCODE='23514';END IF;
   FOREACH field IN ARRAY ARRAY['owner','article','lot','boxes','pallets'] LOOP
    col:=j->'mapping'->>field;IF coalesce(col,'')='' THEN CONTINUE;END IF;
    cellref:=col||(source_row->>'row');
    IF coalesce(src.report::jsonb->'formulas','[]'::jsonb) ? cellref OR coalesce(src.report::jsonb->'errors','[]'::jsonb) ? cellref THEN RAISE EXCEPTION 'Unsafe mapped source cell' USING ERRCODE='23514';END IF;
   END LOOP;
   qty:=CASE WHEN coalesce(j->'mapping'->>'pallets','')='' THEN 1 ELSE replace(coalesce(src.report::jsonb->'numeric_literals'->>((j->'mapping'->>'pallets')||(source_row->>'row')),source_row->'cells'->>(ascii(j->'mapping'->>'pallets')-65)),',','.')::numeric END;
   boxqty:=replace(coalesce(src.report::jsonb->'numeric_literals'->>((j->'mapping'->>'boxes')||(source_row->>'row')),source_row->'cells'->>(ascii(j->'mapping'->>'boxes')-65)),',','.')::numeric;
   IF qty IS NULL OR qty<>trunc(qty) OR qty NOT BETWEEN 1 AND 1000 OR boxqty IS NULL OR boxqty<>trunc(boxqty) OR boxqty NOT BETWEEN 0 AND 2147483647 THEN RAISE EXCEPTION 'Exact known integral pallet and box quantities required' USING ERRCODE='23514';END IF;
   SELECT count(*) INTO expected_count FROM jsonb_array_elements(j->'lines') item WHERE (item->>'source_row')::integer=(source_row->>'row')::integer;
   IF expected_count<>qty OR (SELECT count(DISTINCT (item->>'ordinal')::integer) FROM jsonb_array_elements(j->'lines') item WHERE (item->>'source_row')::integer=(source_row->>'row')::integer AND (item->>'ordinal')::integer BETWEEN 1 AND qty)<>qty THEN RAISE EXCEPTION 'Complete source quantity and ordinals required' USING ERRCODE='23514';END IF;
   total_count:=total_count+qty::integer;
  END LOOP;
  IF total_count<>jsonb_array_length(j->'lines') THEN RAISE EXCEPTION 'No extra or omitted PACKING source lines' USING ERRCODE='23514';END IF;
  FOR x IN SELECT value FROM jsonb_array_elements(j->'lines') LOOP
   SELECT r INTO source_row FROM jsonb_array_elements(src.report::jsonb->'rows') r WHERE (r->>'row')::integer=(x->>'source_row')::integer;
   IF source_row IS NULL OR coalesce(regexp_replace(lower(source_row->'cells'->>((ascii(j->'mapping'->>'owner')-65)::integer)),'[^a-z0-9]','','g'),'') NOT IN('davidneto','ddn','davidnetoddn')
    OR source_row->'cells'->>((ascii(j->'mapping'->>'article')-65)::integer) IS DISTINCT FROM x->>'article'
    OR source_row->'cells'->>((ascii(j->'mapping'->>'lot')-65)::integer) IS DISTINCT FROM x->>'lot'
    OR replace(coalesce(src.report::jsonb->'numeric_literals'->>((j->'mapping'->>'boxes')||(x->>'source_row')),source_row->'cells'->>((ascii(j->'mapping'->>'boxes')-65)::integer)),',','.')::numeric IS DISTINCT FROM (x->>'boxes')::integer THEN RAISE EXCEPTION 'PACKING line differs from mapped DDN source' USING ERRCODE='23514';END IF;
   INSERT INTO cosmo_packing_lines(context_id,packing_id,mapping_id,source_row,ordinal,article,lot,boxes) VALUES(NEW.context_id,h.packing_id,NEW.id,(x->>'source_row')::integer,(x->>'ordinal')::integer,x->>'article',x->>'lot',(x->>'boxes')::integer);
  END LOOP;
  UPDATE cosmo_packing_states SET reference=j->>'reference',reference_key=lower(j->>'reference'),mapping_id=NEW.id,stage='reviewed',version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='confirm' THEN
  IF h.stage<>'reviewed' OR j->>'accepted' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'Reviewed reservation confirmation required' USING ERRCODE='23514';END IF;
  FOR l IN SELECT * FROM cosmo_packing_lines WHERE packing_id=h.packing_id AND mapping_id=h.mapping_id AND context_id=NEW.context_id ORDER BY source_row,ordinal LOOP
   SELECT s.pallet_id INTO selected FROM cosmo_states s JOIN cosmo_pallets a ON a.id=s.pallet_id AND a.context_id=s.context_id JOIN stock st ON st.id=s.stock_id AND st.context_id=s.context_id
    WHERE s.context_id=NEW.context_id AND s.status='approved' AND st.quantidade>0 AND a.article=l.article AND a.lot=l.lot AND s.actual_boxes=l.boxes
    AND NOT EXISTS(SELECT 1 FROM cosmo_packing_picks q WHERE q.context_id=s.context_id AND q.pallet_id=s.pallet_id AND q.active) ORDER BY s.pallet_id LIMIT 1;
   INSERT INTO cosmo_packing_picks(context_id,packing_id,line_id,pallet_id,active,prepared,loaded,version) VALUES(NEW.context_id,h.packing_id,l.id,selected,true,false,false,0);
  END LOOP;
  UPDATE cosmo_packing_states SET stage='preparing',version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='substitute' THEN
  IF h.stage<>'preparing' OR h.loading_started OR k.prepared OR k.loaded THEN RAISE EXCEPTION 'Substitution only in open preparation before physical confirmation' USING ERRCODE='23514';END IF;
  SELECT * INTO b FROM cosmo_states WHERE pallet_id=NEW.pallet_id AND context_id=NEW.context_id;
  SELECT * INTO p FROM cosmo_pallets WHERE id=NEW.pallet_id AND context_id=NEW.context_id;
  IF b.status<>'approved' OR b.id IS NULL OR (p.article,p.lot,b.actual_boxes) IS DISTINCT FROM (l.article,l.lot,l.boxes)
    OR EXISTS(SELECT 1 FROM cosmo_packing_picks WHERE pallet_id=p.id AND context_id=NEW.context_id AND active AND id<>k.id) THEN RAISE EXCEPTION 'Exact available equivalent pallet required' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_picks SET pallet_id=p.id,reason=NULL,version=version+1 WHERE id=k.id;
 ELSIF NEW.kind IN ('prepare','unprepare','exception') THEN
  IF h.stage<>'preparing' OR h.loading_started THEN RAISE EXCEPTION 'Preparation stage closed or loading already started' USING ERRCODE='23514';END IF;
  IF NEW.kind='prepare' THEN
    IF b.id IS NULL OR b.status<>'approved' OR (j->>'lot',(j->>'boxes')::integer) IS DISTINCT FROM (p.lot,b.actual_boxes) OR j->>'checked' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'Physical lot and boxes conference required' USING ERRCODE='23514';END IF;
    IF b.local<>'Zona de preparação' OR z.group_id IS DISTINCT FROM h.packing_id OR z.phase IS DISTINCT FROM 'staged' THEN
      IF j->>'physical' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'Prove movement to the correct preparation group' USING ERRCODE='23514';END IF;
      PERFORM ddn_packing_move(NEW,p.id,'Zona de preparação',h.packing_id,'staged');
    END IF;
    UPDATE cosmo_packing_picks SET prepared=true,prepared_by=u.id,prepared_at=NEW.created_at,reason=NULL,version=version+1 WHERE id=k.id;
  ELSE
    UPDATE cosmo_packing_picks SET prepared=false,reason=NEW.reason,version=version+1 WHERE id=k.id;
  END IF;
 ELSIF NEW.kind='finish_prepare' THEN
  IF h.stage<>'preparing' OR EXISTS(SELECT 1 FROM cosmo_packing_picks WHERE packing_id=h.packing_id AND context_id=NEW.context_id AND NOT prepared AND coalesce(length(btrim(reason)),0)=0) THEN RAISE EXCEPTION 'Explicit reason required for every unprepared pallet' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_states SET stage='prepared',version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='reopen_prepare' THEN
  IF h.stage<>'prepared' OR h.loading_started THEN RAISE EXCEPTION 'Cannot reopen preparation after loading started' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_states SET stage='preparing',version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='start_load' THEN
  IF h.stage<>'prepared' THEN RAISE EXCEPTION 'Closed preparation required before loading' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_states SET stage='loading',loading_started=true,version=version+1 WHERE id=h.id;
 ELSIF NEW.kind IN ('load','unload','return_stage') THEN
  IF h.stage<>'loading' OR NOT k.prepared OR b.status<>'approved' THEN RAISE EXCEPTION 'Only prepared pallets in open loading' USING ERRCODE='23514';END IF;
  IF NEW.kind='load' THEN
   IF (j->>'lot',(j->>'boxes')::integer) IS DISTINCT FROM (p.lot,b.actual_boxes) OR j->>'checked' IS DISTINCT FROM 'true' OR j->>'physical' IS DISTINCT FROM 'true' OR z.group_id<>h.packing_id OR z.phase NOT IN('staged','loading') THEN RAISE EXCEPTION 'Second physical conference and correct preparation group required' USING ERRCODE='23514';END IF;
   IF z.phase='staged' THEN PERFORM ddn_packing_move(NEW,p.id,'Zona de preparação',h.packing_id,'loading');END IF;
   UPDATE cosmo_packing_picks SET loaded=true,loaded_by=u.id,loaded_at=NEW.created_at,reason=NULL,version=version+1 WHERE id=k.id;
  ELSIF NEW.kind='unload' THEN
   UPDATE cosmo_packing_picks SET loaded=false,reason=NEW.reason,version=version+1 WHERE id=k.id;
  ELSE
   IF k.loaded OR z.phase<>'loading' OR j->>'physical' IS DISTINCT FROM 'true' OR (j->>'lot',(j->>'boxes')::integer) IS DISTINCT FROM (p.lot,b.actual_boxes) THEN RAISE EXCEPTION 'Prove actual return to staging after unchecking' USING ERRCODE='23514';END IF;
   PERFORM ddn_packing_move(NEW,p.id,'Zona de preparação',h.packing_id,'staged');
   UPDATE cosmo_packing_picks SET reason=NEW.reason,version=version+1 WHERE id=k.id;
  END IF;
 ELSIF NEW.kind='finish_load' THEN
  IF h.stage<>'loading' OR EXISTS(SELECT 1 FROM cosmo_packing_picks WHERE packing_id=h.packing_id AND context_id=NEW.context_id AND NOT loaded AND coalesce(length(btrim(reason)),0)=0)
    OR EXISTS(SELECT 1 FROM cosmo_packing_picks q JOIN physical_staging_positions zone ON zone.context_id=q.context_id AND zone.unit_kind='cosmopak' AND zone.unit_id=q.pallet_id WHERE q.packing_id=h.packing_id AND q.context_id=NEW.context_id AND NOT q.loaded AND zone.phase='loading') THEN RAISE EXCEPTION 'Explain unshipped pallets and prove returns before closing loading' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_states SET stage='load_closed',version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='reopen_load' THEN
  IF h.stage NOT IN('load_closed','dispatch_reviewed') THEN RAISE EXCEPTION 'Cannot reopen approved dispatch' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_states SET stage='loading',review_id=NULL,version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='review_dispatch' THEN
  IF h.stage<>'load_closed' OR j->>'dispatched_on' IS NULL THEN RAISE EXCEPTION 'Actual dispatch date and closed loading required' USING ERRCODE='23514';END IF;
  UPDATE cosmo_packing_states SET stage='dispatch_reviewed',dispatched_on=(j->>'dispatched_on')::date,review_id=NEW.id,version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='approve' THEN
  IF h.stage<>'dispatch_reviewed' OR (j->>'review_id')::integer IS DISTINCT FROM h.review_id OR j->>'accepted' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'Explicit office dispatch review required' USING ERRCODE='23514';END IF;
  x:=ddn_packing_snapshot(h.packing_id,NEW.context_id);
  FOR slot IN SELECT * FROM cosmo_packing_picks WHERE packing_id=h.packing_id AND context_id=NEW.context_id AND active LOOP
   IF slot.loaded THEN
    IF NOT EXISTS(SELECT 1 FROM physical_staging_positions WHERE context_id=NEW.context_id AND unit_kind='cosmopak' AND unit_id=slot.pallet_id AND group_id=h.packing_id AND phase='loading') THEN RAISE EXCEPTION 'Loaded physical identity changed' USING ERRCODE='23514';END IF;
    PERFORM ddn_packing_move(NEW,slot.pallet_id,'Zona de preparação',h.packing_id,'shipped');
   END IF;
   UPDATE cosmo_packing_picks SET active=false,version=version+1 WHERE id=slot.id;
  END LOOP;
  UPDATE cosmo_packing_states SET stage='shipped',final_snapshot=x::text,version=version+1 WHERE id=h.id;
 ELSIF NEW.kind='return_rack' THEN
  SELECT * INTO b FROM cosmo_states WHERE pallet_id=NEW.pallet_id AND context_id=NEW.context_id;
  SELECT * INTO p FROM cosmo_pallets WHERE id=b.pallet_id AND context_id=NEW.context_id;
  SELECT * INTO z FROM physical_staging_positions WHERE unit_kind='cosmopak' AND unit_id=p.id AND context_id=NEW.context_id;
  IF b.id IS NULL OR b.version<>NEW.expected_version OR b.status<>'approved' OR b.local<>'Zona de preparação' OR z.phase<>'staged' OR z.group_id<>h.packing_id
   OR EXISTS(SELECT 1 FROM cosmo_packing_picks WHERE pallet_id=p.id AND context_id=NEW.context_id AND active)
   OR j->>'physical' IS DISTINCT FROM 'true' OR j->>'checked' IS DISTINCT FROM 'true' OR (j->>'lot',(j->>'boxes')::integer) IS DISTINCT FROM (p.lot,b.actual_boxes) THEN RAISE EXCEPTION 'Unreserved proven return to configured rack required' USING ERRCODE='23514';END IF;
  PERFORM ddn_packing_move(NEW,p.id,j->>'local',h.packing_id,'rack');
 ELSE RAISE EXCEPTION 'Unknown PACKING action' USING ERRCODE='23514';END IF;
 IF scope_pick THEN UPDATE cosmo_packing_states SET version=version+1 WHERE id=h.id;END IF;
 INSERT INTO cosmo_packing_ledger(context_id,action_id,before_state,after_state) VALUES(NEW.context_id,NEW.id,before::text,
 CASE WHEN scope_pick THEN (SELECT to_jsonb(q)::text FROM cosmo_packing_picks q WHERE q.id=k.id) ELSE (SELECT to_jsonb(q)::text FROM cosmo_packing_states q WHERE q.id=h.id) END);
 RETURN NEW;
END $$;
CREATE TRIGGER packing_apply_command AFTER INSERT ON cosmo_packing_actions FOR EACH ROW EXECUTE FUNCTION ddn_packing_command();
-- The global stock history guard already forbids deletes. Retain non-Cosmopak
-- behavior: the function below replaces only Cosmopak's additional veto.
CREATE OR REPLACE FUNCTION ddn_cosmo_stock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM cosmo_states WHERE stock_id=OLD.id AND context_id=OLD.context_id AND status IN('approved','shipped')) THEN
  IF TG_OP='DELETE' OR NOT(current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) AND pg_trigger_depth()>1) THEN RAISE EXCEPTION 'Cosmopak stock changes require an immutable physical command' USING ERRCODE='23514';END IF;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD;END IF;RETURN NEW;
END $$;
CREATE FUNCTION ddn_packing_reconcile() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b cosmo_states;p cosmo_pallets;r cosmo_receptions;rec receipts;live bigint;staged boolean;current_stock stock;
BEGIN
 -- Deferred events contain earlier row images; reconcile the final committed balance.
 SELECT * INTO current_stock FROM stock WHERE id=NEW.id AND context_id=NEW.context_id;
 NEW:=current_stock;
 IF NOT EXISTS(SELECT 1 FROM cosmo_states WHERE context_id=NEW.context_id AND stock_id=NEW.id)
 AND NOT EXISTS(SELECT 1 FROM physical_location_state h JOIN physical_locations x ON x.id=h.physical_id WHERE x.label=NEW.local AND h.context_id=NEW.context_id AND h.source_kind='cosmopak') THEN RETURN NEW;END IF;
 SELECT count(*) INTO live FROM cosmo_states WHERE context_id=NEW.context_id AND stock_id=NEW.id AND status='approved';
 IF NEW.quantidade<>live THEN RAISE EXCEPTION 'Integral Cosmopak approval required; exact unit balance missing' USING ERRCODE='23514';END IF;
 FOR b IN SELECT * FROM cosmo_states WHERE context_id=NEW.context_id AND stock_id=NEW.id AND status='approved' LOOP
  SELECT * INTO p FROM cosmo_pallets WHERE id=b.pallet_id AND context_id=NEW.context_id;
  SELECT * INTO r FROM cosmo_receptions WHERE id=p.reception_id AND context_id=NEW.context_id;
  SELECT * INTO rec FROM receipts WHERE id=r.receipt_id AND context_id=NEW.context_id;
  IF r.status<>'approved' OR rec.id IS NULL OR rec.status<>'confirmed' OR (NEW.artigo,NEW.lote,NEW.carga,NEW.local,NEW.stacked) IS DISTINCT FROM (p.article,p.lot,rec.code,b.local,false) THEN RAISE EXCEPTION 'Integral Cosmopak approval and original load required' USING ERRCODE='23514';END IF;
  IF NEW.local='Zona de preparação' THEN
   IF NOT EXISTS(SELECT 1 FROM physical_staging_positions WHERE context_id=NEW.context_id AND unit_kind='cosmopak' AND unit_id=p.id AND phase IN('staged','loading')) THEN RAISE EXCEPTION 'Shared staging presence not proven' USING ERRCODE='23514';END IF;
  ELSIF live<>1 OR NOT EXISTS(SELECT 1 FROM physical_locations x JOIN physical_location_state h ON h.physical_id=x.id WHERE x.label=b.local AND h.context_id=NEW.context_id AND h.state='occupied' AND h.stock_id=NEW.id) THEN RAISE EXCEPTION 'Shared rack occupancy not reconciled' USING ERRCODE='23514';END IF;
 END LOOP;
 RETURN NEW;
END $$;
DROP TRIGGER cosmo_integral_stock ON stock;
CREATE CONSTRAINT TRIGGER cosmo_integral_stock AFTER INSERT OR UPDATE ON stock DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ddn_packing_reconcile();
CREATE FUNCTION ddn_packing_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE h cosmo_packing_states;
BEGIN
 SELECT * INTO h FROM cosmo_packing_states WHERE packing_id=NEW.packing_id AND context_id=NEW.context_id;
 IF NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.actor_id AND active AND role='admin') OR h.id IS NULL OR NEW.digest<>encode(sha256(NEW.pdf),'hex')
 OR (NEW.kind='dispatch' AND (h.stage<>'shipped' OR NEW.snapshot::jsonb IS DISTINCT FROM h.final_snapshot::jsonb))
 OR (NEW.kind='stage' AND (h.stage IN('draft','reviewed','shipped') OR NEW.snapshot::jsonb IS DISTINCT FROM ddn_packing_snapshot(h.packing_id,NEW.context_id)))
 OR NEW.kind NOT IN('stage','dispatch') THEN RAISE EXCEPTION 'Current office snapshot and immutable PDF required' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE TRIGGER packing_document_guard BEFORE INSERT ON cosmo_packing_documents FOR EACH ROW EXECUTE FUNCTION ddn_packing_document();

CREATE TRIGGER cosmo_packing_states_protected BEFORE INSERT OR UPDATE OR DELETE ON cosmo_packing_states FOR EACH ROW EXECUTE FUNCTION ddn_packing_projection();

CREATE TRIGGER cosmo_packing_lines_protected BEFORE INSERT OR UPDATE OR DELETE ON cosmo_packing_lines FOR EACH ROW EXECUTE FUNCTION ddn_packing_projection();

CREATE TRIGGER cosmo_packing_picks_protected BEFORE INSERT OR UPDATE OR DELETE ON cosmo_packing_picks FOR EACH ROW EXECUTE FUNCTION ddn_packing_projection();

CREATE TRIGGER cosmo_packing_ledger_protected BEFORE INSERT OR UPDATE OR DELETE ON cosmo_packing_ledger FOR EACH ROW EXECUTE FUNCTION ddn_packing_projection();

CREATE TRIGGER cosmo_packing_moves_protected BEFORE INSERT OR UPDATE OR DELETE ON cosmo_packing_moves FOR EACH ROW EXECUTE FUNCTION ddn_packing_projection();

CREATE TRIGGER physical_staging_positions_protected BEFORE INSERT OR UPDATE OR DELETE ON physical_staging_positions FOR EACH ROW EXECUTE FUNCTION ddn_packing_projection();
CREATE TRIGGER cosmo_packings_immutable BEFORE UPDATE OR DELETE ON cosmo_packings FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_lines_immutable BEFORE UPDATE OR DELETE ON cosmo_packing_lines FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_actions_immutable BEFORE UPDATE OR DELETE ON cosmo_packing_actions FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_ledger_immutable BEFORE UPDATE OR DELETE ON cosmo_packing_ledger FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_moves_immutable BEFORE UPDATE OR DELETE ON cosmo_packing_moves FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_documents_immutable BEFORE UPDATE OR DELETE ON cosmo_packing_documents FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_staging_areas_immutable BEFORE UPDATE OR DELETE ON physical_staging_areas FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_staging_events_immutable BEFORE UPDATE OR DELETE ON physical_staging_events FOR EACH ROW EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_moves_no_truncate BEFORE TRUNCATE ON cosmo_packing_moves FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_staging_events_no_truncate BEFORE TRUNCATE ON physical_staging_events FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_lines_no_truncate BEFORE TRUNCATE ON cosmo_packing_lines FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_ledger_no_truncate BEFORE TRUNCATE ON cosmo_packing_ledger FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_actions_no_truncate BEFORE TRUNCATE ON cosmo_packing_actions FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_staging_areas_no_truncate BEFORE TRUNCATE ON physical_staging_areas FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_picks_no_truncate BEFORE TRUNCATE ON cosmo_packing_picks FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER physical_staging_positions_no_truncate BEFORE TRUNCATE ON physical_staging_positions FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packings_no_truncate BEFORE TRUNCATE ON cosmo_packings FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_states_no_truncate BEFORE TRUNCATE ON cosmo_packing_states FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
CREATE TRIGGER cosmo_packing_documents_no_truncate BEFORE TRUNCATE ON cosmo_packing_documents FOR EACH STATEMENT EXECUTE FUNCTION wms_append_only();
