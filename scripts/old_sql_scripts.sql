-- DROP FUNCTION z_tree.get_ancestry(_text);

CREATE OR REPLACE FUNCTION z_tree.get_ancestry(doc_ids text[])
 RETURNS TABLE(branch_id bigint)
 LANGUAGE sql
AS $function$
/* to pull all the branch_ids from a particular list of starting doc_ids
doesn't return the paths, just the full list of branch_ids
-- query with: 
SELECT *
FROM get_ancestry(
  (SELECT array_agg(DISTINCT doc_id) FROM subquery)
);
|branch_id|
|---------|
|35       |
|67       |
|96       |

*/
WITH RECURSIVE
start_children AS (
  SELECT unnest(doc_ids) AS doc_id
),ancestry AS (
  -- anchor: start from the given child
  select distinct
    r.branch_id
    , r.tree_id
    --0 AS depth
  FROM z_tree.v_tree_map r
  inner JOIN start_children sc
    ON r.doc_id = sc.doc_id         -- anchor = your subset only
  
  UNION 

  -- recursive step: move from child to its parent row
  -- cluster z_mod corresponds to branch number
  SELECT
    distinct r.branch_id
    , r.tree_id
    --a.depth + 1
  FROM z_tree.v_tree_map r
  join ancestry a
    on r.z_mod = a.branch_id and a.tree_id=r.tree_id
)
SELECT distinct branch_id
FROM ancestry;
$function$
;


-- DROP FUNCTION z_tree.get_ancestry_paths(_int8);

CREATE OR REPLACE FUNCTION z_tree.get_ancestry_paths(z_mods bigint[])
 RETURNS TABLE(z_mod bigint, branch_id bigint, depth bigint, root_z_mod bigint)
 LANGUAGE sql
AS $function$
/* to pull all the branch_ids from a particular list of starting z_id(documents)
-- returns the paths with depth
-- query with: 
SELECT *
FROM get_ancestry_paths(
  (SELECT array_agg(DISTINCT z_id) FROM subquery)
);
|start_id|z_id|branch_id|depth|
|--------|----|---------|-----|
|18      |18  |35       |0    |
|18      |35  |51       |1    |
|18      |51  |83       |2    |
|18      |83  |99       |3    |

*/
WITH RECURSIVE
start_children AS (
  SELECT unnest(z_mods) AS z_mod
),
ancestry AS (
  SELECT
    r.z_mod,
    r.branch_id,
    0        AS depth,
    r.z_mod   AS root_z_mod
  FROM z_tree.v_tree_map AS r
  JOIN start_children AS sc
    ON r.z_mod = sc.z_mod

  UNION ALL

  SELECT
    r.z_mod,
    r.branch_id,
    a.depth + 1 AS depth,
    a.root_z_mod
  FROM z_tree.v_tree_map AS r
  JOIN ancestry AS a
    ON r.z_mod = a.branch_id
)
SELECT z_mod, branch_id, depth, root_z_mod
FROM ancestry;
$function$
;


-- DROP FUNCTION z_tree.get_ancestry_paths(_text, text);

CREATE OR REPLACE FUNCTION z_tree.get_ancestry_paths(doc_ids text[], p_tree_id text)
 RETURNS TABLE(z_mod bigint, z_id bigint, doc_id text, branch_id bigint, depth bigint, root_z_mod bigint, tree_id text, coph_distance double precision, doc_count bigint)
 LANGUAGE sql
AS $function$
/* to pull all the branch_ids from a particular list of starting z_id(documents)
-- returns the paths with depth
-- query with: 
SELECT *
FROM get_ancestry_paths(
  (SELECT array_agg(DISTINCT doc_id) FROM subquery)
);
|start_id|z_id|branch_id|depth|
|--------|----|---------|-----|
|18      |18  |35       |0    |
|18      |35  |51       |1    |
|18      |51  |83       |2    |
|18      |83  |99       |3    |

*/
WITH RECURSIVE
start_children AS (
  SELECT p_tree_id as tree_id, unnest(doc_ids) AS doc_id
),
ancestry AS (
  SELECT
    r.z_mod,
    r.z_id,
    r.doc_id,
    r.branch_id,
    0        AS depth,
    r.z_mod   AS root_z_mod
    ,r.tree_id
    ,r.coph_distance
    , r.doc_count as doc_count
    
  FROM z_tree.v_tree_map AS r
  JOIN start_children AS sc
    ON r.doc_id = sc.doc_id and sc.tree_id=r.tree_id

  UNION ALL

  SELECT
    r.z_mod,
    r.z_id,
    r.doc_id,
    r.branch_id,
    a.depth + 1 AS depth,
    a.root_z_mod
    ,r.tree_id
    ,r.coph_distance
    ,r.doc_count as doc_count
    
  FROM z_tree.v_tree_map AS r
  JOIN ancestry AS a
    ON r.z_mod = a.branch_id and r.tree_id =a.tree_id
)
SELECT z_mod,z_id,doc_id, branch_id, depth, root_z_mod, tree_id, coph_distance, doc_count
FROM ancestry;
$function$
;
-- DROP FUNCTION z_tree.get_compressed_subtree(_text);

CREATE OR REPLACE FUNCTION z_tree.get_compressed_subtree(doc_ids text[])
 RETURNS TABLE(l_node bigint, r_node bigint, coph_distance double precision, doc_count bigint, branch_id bigint, l_doc_id text, r_doc_id text, branch_doc_id text, tree_id text)
 LANGUAGE sql
AS $function$
/* 	-- generate the subtree - only selected nodes and ancestors

-- query with: 
SELECT *
FROM get_subtree(
  (SELECT array_agg(DISTINCT doc_ids::text) FROM subquery)
);
|branch_id|l_node|r_node|coph_distance|doc_count|l_doc_id                            |r_doc_id                            |tree_id                             |n_docs|l_mod|r_mod|l_leaf|r_leaf|
|---------|------|------|-------------|---------|------------------------------------|------------------------------------|------------------------------------|------|-----|-----|------|------|
|0        |17    |132   |0.0229150319 |2        |b61e0cb4-6fb7-4a3e-a91c-d91709496299|33aff4d2-9d89-4c9b-878d-e754d0d770a0|418af38d-ff23-4c83-a7c2-c8afc8e28477|144   |-18  |-133 |true  |true  |
|1        |33    |78    |0.0313533685 |2        |0f49ae76-b014-4b22-bf26-d227a4360330|17de6ee5-1a1e-498d-bc9e-4f9d58cb6270|418af38d-ff23-4c83-a7c2-c8afc8e28477|144   |-34  |-79  |true  |true  |


*/
WITH 
	doc_list AS (
		  SELECT unnest(doc_ids) AS doc_id
		)
	, base as (select *  FROM z_tree.get_ancestry_paths(
	  (SELECT array_agg(distinct doc_id::text) from doc_list)
	) sb)
	, d_count as (
		select (select max(case when sign(z_mod)=sign(z_id) then z_id-z_mod end) doc_count from base)
		, (select count(*) new_count from doc_list))
	, linkages as (
	select *, min(z_mod) over (partition by roots) minbranch
	, max(branch_id) over (partition by roots) as maxbranch, cardinality(roots) cards
		from (select z_mod, branch_id ,z_id, doc_id,is_root,coph_distance,tree_id, array_agg(root_z_mod order by root_z_mod) roots
		from 
			(select  z_mod,z_id,doc_id, branch_id, root_z_mod, coph_distance,tree_id , count(*) b_count
			, case when z_mod=root_z_mod then true else false end is_root
			from base group by z_mod, z_id,doc_id, branch_id, root_z_mod, coph_distance,tree_id) sb
		--where  b_count>1 
		group by z_mod, branch_id, z_id, doc_id, is_root, coph_distance,tree_id) sb2
		--where cardinality(roots)>1
	order by branch_id
	)
	--select * from linkages
	, mapping as (
		select *, row_number() over (partition by newmax order by newmin asc) l_ranker
		, row_number() over (partition by newmax order by newmin desc) as r_ranker
		
		from 
			(select *, case when minbranch<0 then minbranch*-1 -1 else minbranch+doc_count end as oldmin, 
			case when maxbranch<0 then maxbranch*-1 -1 else maxbranch+doc_count end as oldmax
			, case when minbranch<0 then 0 else new_count end + dense_rank() over (partition by is_root order by minbranch) as newmin
			,new_count + dense_rank() over (order by maxbranch) as newmax
			from linkages, d_count where z_mod=minbranch
	) sq)
	
	--select * from mapping
	
	-- this will be how i track from tree_map to tree_z
	select sq1.l_node,sq1.r_node, sq1.coph_distance,sq1.n_docs as doc_count,sq1.branch_id,sq1.l_doc_id,sq1.r_doc_id
	,  case when b.doc_id is null then 'root' else b.doc_id end as branch_doc_id
	, sq1.tree_id
	from
	(select min(case when l_ranker = 1 then newmin else null end) l_node,max(case when r_ranker = 1 then newmin else null end) r_node
		, sum(cards) n_docs,max(coph_distance) coph_distance,newmax as branch_id, maxbranch
		, max(case when l_ranker = 1 then doc_id else null end) l_doc_id, max(case when r_ranker = 1 then doc_id else null end) r_doc_id
		, tree_id
		from mapping group by maxbranch, newmax, tree_id
		having min(minbranch) <> max(minbranch) ) sq1
	left join (select distinct z_mod, tree_id, doc_id from base) b on sq1.maxbranch=b.z_mod and b.tree_id=sq1.tree_id
$function$
;

-- DROP FUNCTION z_tree.get_submap(_int8);

CREATE OR REPLACE FUNCTION z_tree.get_submap(z_mods bigint[])
 RETURNS TABLE(tree_id text, doc_id text, z_id bigint, leaf boolean, branch_id bigint, z_mod bigint, b_side text)
 LANGUAGE sql
AS $function$
/* 	-- generate the subtree - only selected nodes and ancestors

-- query with: 
SELECT *
FROM get_submap(
  (SELECT array_agg(DISTINCT z_mod) FROM subquery)
);
|tree_id                             |doc_id                              |z_id|leaf|branch_id|z_mod|b_side|
|------------------------------------|------------------------------------|----|----|---------|-----|------|
|418af38d-ff23-4c83-a7c2-c8afc8e28477|fa02b4d1-2152-41fb-8939-1c80d178f9f1|134 |true|89       |-135 |r     |
|418af38d-ff23-4c83-a7c2-c8afc8e28477|e70c95cb-baea-4a95-ac5c-bc608183be62|99  |true|18       |-100 |l     |

*/
WITH RECURSIVE
	doc_list AS (
		  SELECT unnest(z_mods) AS z_mod
		)

	,branch_list as 
		(SELECT branch_id
		FROM z_tree.get_ancestry(
		  (SELECT array_agg(DISTINCT z_mod) FROM doc_list)
		)
	)
	
-- END WITH

select tree_id,doc_id,z_id,leaf,branch_id, z_mod, b_side
	from z_tree.v_tree_map tz
	where tz.branch_id in (select branch_id from branch_list);
$function$
;
-- DROP FUNCTION z_tree.get_subtree(_text);

CREATE OR REPLACE FUNCTION z_tree.get_subtree(doc_ids text[])
 RETURNS TABLE(l_node bigint, r_node bigint, coph_distance double precision, doc_count bigint, branch_id bigint, l_doc_id text, r_doc_id text, tree_id text, n_docs bigint, l_mod bigint, r_mod bigint, l_leaf boolean, r_leaf boolean)
 LANGUAGE sql
AS $function$
/* 	-- generate the subtree - only selected nodes and ancestors

-- query with: 
SELECT *
FROM get_subtree(
  (SELECT array_agg(DISTINCT doc_ids::text) FROM subquery)
);
|branch_id|l_node|r_node|coph_distance|doc_count|l_doc_id                            |r_doc_id                            |tree_id                             |n_docs|l_mod|r_mod|l_leaf|r_leaf|
|---------|------|------|-------------|---------|------------------------------------|------------------------------------|------------------------------------|------|-----|-----|------|------|
|0        |17    |132   |0.0229150319 |2        |b61e0cb4-6fb7-4a3e-a91c-d91709496299|33aff4d2-9d89-4c9b-878d-e754d0d770a0|418af38d-ff23-4c83-a7c2-c8afc8e28477|144   |-18  |-133 |true  |true  |
|1        |33    |78    |0.0313533685 |2        |0f49ae76-b014-4b22-bf26-d227a4360330|17de6ee5-1a1e-498d-bc9e-4f9d58cb6270|418af38d-ff23-4c83-a7c2-c8afc8e28477|144   |-34  |-79  |true  |true  |


*/
WITH 
	doc_list AS (
		  SELECT unnest(doc_ids) AS doc_id
		)

	,branch_list as 
		(SELECT branch_id
		FROM z_tree.get_ancestry(
		  (SELECT array_agg(DISTINCT doc_id) FROM doc_list)
		)
	)
	
-- END WITH

select l_node, tz.r_node,
	coph_distance, doc_count, branch_id, tz.l_doc_id, tz.r_doc_id, tree_id
	, n_docs, l_mod, r_mod,l_leaf, r_leaf
	from z_tree.v_tree_z tz
	where tz.branch_id in (select branch_id from branch_list);
$function$
;
-- DROP FUNCTION z_tree.insert_doc_into_tree(uuid, uuid, uuid, numeric, int4);

CREATE OR REPLACE FUNCTION z_tree.insert_doc_into_tree(p_tree_id uuid, p_new_doc_id uuid, p_target_doc_id uuid, p_new_distance numeric, p_new_cnt integer)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
/* 	-- generate the subtree - only selected nodes and ancestors

-- call with: 
SELECT z_tree.insert_doc_into_tree(
  '418af38d-ff23-4c83-a7c2-c8afc8e28477',
  '32d230f6-1886-4ae7-8e9c-d4d99569163d',
  '5a7958b9-31a4-45c5-8865-ea38fefdb3e3',
  0.050,
  1
);


*/
DECLARE
  did_something boolean := false;

BEGIN
drop table if exists target_map;
CREATE TEMP TABLE target_map AS
 WITH new_insert AS (
    SELECT
      p_tree_id::text       AS tree_id,
      p_new_doc_id::text    AS new_doc_id,
      p_target_doc_id::text AS target_doc_id,
      p_new_distance  AS new_distance,
      p_new_cnt       AS new_cnt
  )
SELECT
  vtm.*,
  ni.new_doc_id,
  ni.new_distance,
  ni.new_cnt
FROM z_tree.v_tree_map vtm
JOIN new_insert ni
  ON vtm.tree_id = ni.tree_id
 AND vtm.doc_id = ni.target_doc_id;

-- #1 bump every l_node and r_node and branch_id where corresponding z_mod>11 (branch_id)
--BEGIN;
DROP TABLE IF EXISTS tmp_tree_z;

-- 1) Compute all new rows in a staging table (no PK here)
CREATE TEMP TABLE tmp_tree_z AS
SELECT
  tz.tree_id,
  -- old key kept so we know what to delete
  tz.branch_id              AS old_branch_id,

  /* compute new_branch_id and new l_node/r_node with your logic */
  tz.branch_id + tm.new_cnt +
    CASE WHEN tz.branch_id >= tm.branch_id THEN 1 ELSE 0 END
    AS new_branch_id,
	tz.l_node as old_l_node,
  tz.l_node + CASE
                WHEN vtz.l_leaf = false THEN
                  CASE WHEN vtz.l_mod >= tm.branch_id
                         THEN tm.new_cnt + 1
                       ELSE tm.new_cnt
                  END
                ELSE 0
              END          AS new_l_node,
tz.r_node as old_r_node,
  tz.r_node + CASE
                WHEN vtz.r_leaf = false THEN
                  CASE WHEN vtz.r_mod >= tm.branch_id
                         THEN tm.new_cnt + 1
                       ELSE tm.new_cnt
                  END
                ELSE 0
              END          AS new_r_node,

  tz.coph_distance,
  tz.doc_count,
  tz.l_doc_id,
  tz.r_doc_id
FROM z_tree.tree_z   tz
JOIN z_tree.v_tree_z vtz
  ON tz.tree_id   = vtz.tree_id
 AND tz.branch_id = vtz.branch_id
JOIN target_map tm
  ON tz.tree_id   = tm.tree_id;

 -- verify that the branch doc_ids contain the same doc_ids before and after.
 -- just node numbers move, not content
 if exists (
  select 1--vtm.z_mod, vtm.z_id,vtm.doc_id as branch_doc_id, tz.l_doc_id, tz.r_doc_id 
from z_tree.v_tree_map vtm 
inner join tmp_tree_z ttz 
	on vtm.z_mod=ttz.old_branch_id and ttz.tree_id=vtm.tree_id
inner join z_tree.tree_z tz 
	on vtm.tree_id=tz.tree_id and vtm.z_mod=tz.branch_id 
where tz.l_doc_id<>ttz.l_doc_id and tz.r_doc_id<>ttz.r_doc_id
 and vtm.leaf = false  
) THEN
RAISE EXCEPTION 'branch doc_ids don not line up in tmp_tree_z';
  END IF;

-- Optional: verify uniqueness of the new key
/*SELECT tree_id, new_branch_id, COUNT(*)
FROM tmp_tree_z
GROUP BY tree_id, new_branch_id
HAVING COUNT(*) > 1;
*/
-- must return zero rows, otherwise your math still collides
-- create the insert for the bump table before altering tree_z
drop table if exists tmp_bump_branch;
create temp table tmp_bump_branch as
select tm.branch_id+1 as new_branch
, case when bump.z_mod<0 then bump.z_mod*-1 -1 else bump.z_mod+new_cnt+n_docs end new_l
, branch.z_mod+new_cnt+n_docs new_r
, tz.coph_distance, tz.doc_count+new_cnt as doc_count
, bump.doc_id l_doc_id, branch.doc_id r_doc_id
, tm.tree_id
from z_tree.v_tree_map bump join target_map tm 
on bump.branch_id = tm.branch_id and bump.doc_id<>tm.doc_id and bump.tree_id =tm.tree_id
	join z_tree.v_tree_map branch on tm.branch_id=branch.z_mod and branch.tree_id =tm.tree_id
	join z_tree.v_tree_z tz on tm.tree_id=tz.tree_id and tm.branch_id=tz.branch_id;

-- 2) Delete old rows that are being moved
DELETE FROM z_tree.tree_z tz
USING tmp_tree_z t
WHERE tz.tree_id   = t.tree_id
  AND tz.branch_id = t.old_branch_id;

-- 3) Insert the new versions (now PK sees only the new set)
INSERT INTO z_tree.tree_z (
  tree_id,
  branch_id,
  l_node,
  r_node,
  coph_distance,
  doc_count,
  l_doc_id,
  r_doc_id
)
SELECT
  tree_id,
  new_branch_id,
  new_l_node,
  new_r_node,
  coph_distance,
  doc_count,
  l_doc_id,
  r_doc_id
FROM tmp_tree_z;

--Rollback;


-- insert new branch with bumped node and branch_node to make room for next insert
insert into z_tree.tree_z
select * from tmp_bump_branch;
--returning *;


-- update old branch_id with  new details (new id and target id) (not worry about more right now)	
update z_tree.tree_z tz 
--select 	--tm.branch_id as new_branch
--,tm.z_mod
set l_node = tm.z_id
, r_node = vtz.n_docs+new_cnt-1 
, coph_distance = tm.new_distance
, doc_count = tz.doc_count+new_cnt-1 
, l_doc_id = tm.doc_id 
, r_doc_id = tm.new_doc_id
--, tm.tree_id
from  target_map tm join z_tree.v_tree_z vtz on tm.tree_id=vtz.tree_id and tm.branch_id=vtz.branch_id
where tm.tree_id=tz.tree_id and tm.branch_id=tz.branch_id
returning 1 into did_something;
--returning *;

  IF did_something THEN
    RETURN true;
  ELSE
    RETURN false;
  END IF;

end;
$function$
;
-- DROP FUNCTION z_tree.insert_doc_into_tree(uuid, uuid, uuid, bool, numeric, int4);

CREATE OR REPLACE FUNCTION z_tree.insert_doc_into_tree(p_tree_id uuid, p_new_doc_id uuid, p_target_doc_id uuid, p_leaf boolean, p_new_distance numeric, p_new_cnt integer)
 RETURNS boolean
 LANGUAGE plpgsql
AS $function$
/* 	-- generate the subtree - only selected nodes and ancestors

-- call with: 
SELECT z_tree.insert_doc_into_tree(
  '418af38d-ff23-4c83-a7c2-c8afc8e28477',
  '32d230f6-1886-4ae7-8e9c-d4d99569163d',
  '5a7958b9-31a4-45c5-8865-ea38fefdb3e3',
  0.050,
  1
);


*/
DECLARE
  did_something boolean := false;

BEGIN
drop table if exists target_map;
CREATE TEMP TABLE target_map AS
 WITH new_insert AS (
    SELECT
      p_tree_id::text       AS tree_id,
      p_new_doc_id::text    AS new_doc_id,
      p_target_doc_id::text AS target_doc_id,
      p_leaf::bool as new_leaf,
      p_new_distance  AS new_distance,
      p_new_cnt::bigint       AS new_cnt
  )
SELECT
  vtm.*,
  ni.new_doc_id,
  ni.new_distance,
  ni.new_cnt,
  ni.new_leaf
FROM z_tree.v_tree_map vtm
JOIN new_insert ni
  ON vtm.tree_id = ni.tree_id
 AND vtm.doc_id = ni.target_doc_id;

-- create a new row to insert 
DROP TABLE IF EXISTS tmp_new_row;
CREATE TEMP TABLE tmp_new_row AS 
select tm.branch_id+new_cnt
, z_id+new_cnt as l_node, 
 min_z_mod * -1 as r_node -- biggest negative z_mod 
 ,new_distance
 , doc_count+new_cnt as doc_count
 , tm.doc_id as l_doc_id, tm.new_doc_id as r_doc_id
 ,tm.tree_id, tm.leaf, tm.new_leaf
   
 from target_map tm
 join (select mmod.tree_id, min(mmod.z_mod) min_z_mod
 	from z_tree.v_tree_map mmod 
 	join target_map on mmod.tree_id=target_map.tree_id group by mmod.tree_id) vtm
 	on vtm.tree_id=tm.tree_id;

-- #1 bump every l_node and r_node and branch_id where corresponding z_mod>11 (branch_id)
--BEGIN;
DROP TABLE IF EXISTS tmp_tree_z;

-- 1) Compute all new rows in a staging table (no PK here)
CREATE TEMP TABLE tmp_tree_z AS
SELECT
  tz.tree_id,
  -- old key kept so we know what to delete
  tz.branch_id              AS old_branch_id,

  /* compute new_branch_id and new l_node/r_node with your logic */
  tz.branch_id + tm.new_cnt +
    CASE WHEN tz.branch_id > tm.branch_id THEN 2 ELSE 0 END
    AS new_branch_id,
	tz.l_node as old_l_node,
  tz.l_node + CASE
                WHEN vtz.l_leaf = false THEN
                  CASE WHEN vtz.l_mod > tm.branch_id
                         THEN tm.new_cnt + 2
                       ELSE tm.new_cnt
                  END
                ELSE 0
              END          AS new_l_node,
tz.r_node as old_r_node,
  tz.r_node + CASE
                WHEN vtz.r_leaf = false THEN
                  CASE WHEN vtz.r_mod > tm.branch_id
                         THEN tm.new_cnt + 2
                       ELSE tm.new_cnt
                  END
                ELSE 0
              END          AS new_r_node,

  tz.coph_distance,
  tz.doc_count,
  tz.l_doc_id,
  tz.r_doc_id,
  tz.l_leaf,
  tz.r_leaf
FROM z_tree.tree_z   tz
JOIN z_tree.v_tree_z vtz
  ON tz.tree_id   = vtz.tree_id
 AND tz.branch_id = vtz.branch_id
JOIN target_map tm
  ON tz.tree_id   = tm.tree_id and tm.branch_id<>tz.branch_id;

 -- verify that the branch doc_ids contain the same doc_ids before and after.
 -- just node numbers move, not content
 if exists (
  select 1--vtm.z_mod, vtm.z_id,vtm.doc_id as branch_doc_id, tz.l_doc_id, tz.r_doc_id 
from z_tree.v_tree_map vtm 
inner join tmp_tree_z ttz 
	on vtm.z_mod=ttz.old_branch_id and ttz.tree_id=vtm.tree_id
inner join z_tree.tree_z tz 
	on vtm.tree_id=tz.tree_id and vtm.z_mod=tz.branch_id 
where tz.l_doc_id<>ttz.l_doc_id and tz.r_doc_id<>ttz.r_doc_id
 and vtm.leaf = false  
) THEN
RAISE EXCEPTION 'branch doc_ids don not line up in tmp_tree_z';
  END IF;

-- Optional: verify uniqueness of the new key
/*SELECT tree_id, new_branch_id, COUNT(*)
FROM tmp_tree_z
GROUP BY tree_id, new_branch_id
HAVING COUNT(*) > 1;
*/
-- must return zero rows, otherwise your math still collides
-- create the insert for the bump table before altering tree_z
drop table if exists tmp_bump_branch;
create temp table tmp_bump_branch as
select tm.branch_id+new_cnt+1 as new_branch
--, case when bump.z_mod<0 then bump.z_mod*-1 -1 else bump.z_id+new_cnt end new_l
 ,CASE
        WHEN bump.leaf = false THEN
          bump.z_id + CASE WHEN bump.z_mod > tm.branch_id
                 THEN tm.new_cnt + 2
               ELSE tm.new_cnt
          END
        ELSE bump.z_id
          END          AS new_l
, branch.z_id+new_cnt -- same as the new branch_id of the main insert
, tz.coph_distance, tz.doc_count+new_cnt as doc_count
, bump.doc_id l_doc_id, branch.doc_id r_doc_id
, tm.tree_id
, bump.leaf l_leaf, false as r_leaf
from z_tree.v_tree_map bump join target_map tm 
on bump.branch_id = tm.branch_id and bump.doc_id<>tm.doc_id and bump.tree_id =tm.tree_id
	join z_tree.v_tree_map branch on tm.branch_id=branch.z_mod and branch.tree_id =tm.tree_id
	join z_tree.v_tree_z tz on tm.tree_id=tz.tree_id and tm.branch_id=tz.branch_id;

-- 2) Delete old rows that are being moved
DELETE FROM z_tree.tree_z tz
USING tmp_tree_z t
WHERE tz.tree_id   = t.tree_id;
  --AND tz.branch_id = t.old_branch_id;

-- 3) Insert the new versions (now PK sees only the new set)
INSERT INTO z_tree.tree_z (
  tree_id,
  branch_id,
  l_node,
  r_node,
  coph_distance,
  doc_count,
  l_doc_id,
  r_doc_id,
  l_leaf,
  r_leaf
)
SELECT
  tree_id,
  new_branch_id,
  new_l_node,
  new_r_node,
  coph_distance,
  doc_count,
  l_doc_id,
  r_doc_id,
  l_leaf,
  r_leaf
FROM tmp_tree_z;

--Rollback;
if exists (
select 1 
from z_tree.v_tree_map where tree_id in (select distinct tree_id from target_map)
group by doc_id
having count(*)>1)
-- update old branch_id with  new details (new id and target id) (not worry about more right now)	
THEN
RAISE EXCEPTION 'duplicate doc_id in v_tree_map before bump';
  END IF;


-- insert new branch with bumped node and branch_node to make room for next insert
INSERT INTO z_tree.tree_z 
select * from tmp_bump_branch;
--returning *;

if exists (
select 1 
from z_tree.v_tree_map where tree_id in (select distinct tree_id from target_map)
group by doc_id
having count(*)>1)
-- update old branch_id with  new details (new id and target id) (not worry about more right now)	
THEN
RAISE EXCEPTION 'duplicate doc_id in v_tree_map after bump';
  END IF;


INSERT INTO z_tree.tree_z 
select * from tmp_new_row
returning 1 into did_something;

if exists (
select 1 
from z_tree.v_tree_map where tree_id in (select distinct tree_id from target_map)
group by doc_id
having count(*)>1)
-- update old branch_id with  new details (new id and target id) (not worry about more right now)	
THEN
RAISE EXCEPTION 'duplicate doc_id in v_tree_map';
  END IF;

/*
update z_tree.tree_z tz 
--select 	--tm.branch_id as new_branch
--,tm.z_mod
set l_node = tm.z_id
, r_node = vtz.n_docs+new_cnt-1 
, coph_distance = tm.new_distance
, doc_count = tz.doc_count+new_cnt-1 
, l_doc_id = tm.doc_id 
, r_doc_id = tm.new_doc_id
, l_leaf = tm.leaf
, r_leaf = tm.new_leaf
--, tm.tree_id
from  target_map tm join z_tree.v_tree_z vtz on tm.tree_id=vtz.tree_id and tm.branch_id=vtz.branch_id
where tm.tree_id=tz.tree_id and tm.branch_id=tz.branch_id
returning 1 into did_something;
--returning *;
*/
  IF did_something THEN
    RETURN true;
  ELSE
    RETURN false;
  END IF;

end;
$function$
;

