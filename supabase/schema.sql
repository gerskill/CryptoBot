-- CryptoBot : résumés publiés par le Mac pour l'app iPhone, et commandes envoyées depuis l'iPhone
-- À coller dans Supabase → SQL Editor → New query, puis « Run ». Peut être relancé sans risque.
--
-- Une ligne par « clé » et par utilisateur, réécrite toutes les 2 min par
-- scripts/publie_supabase.py avec le compte de Killian (jamais la clé secrète du projet) :
--   etat, positions, perf_7d, perf_30d, perf_all, liquid, alertes
-- L'app (mobile/) lit cette table. Aucune suppression n'est autorisée.
-- Contrat des champs : docs/superpowers/plans/2026-11-18-app-iphone.md.

create table if not exists public.snapshots (
  user_id      uuid        not null default auth.uid() references auth.users (id) on delete cascade,
  key          text        not null check (char_length(key) between 1 and 64),
  value        jsonb       not null,
  published_at timestamptz not null default now(),
  primary key (user_id, key)
);

-- Chacun ne voit et n'écrit que ses propres lignes
alter table public.snapshots enable row level security;

drop policy if exists "snapshots: lire ses lignes"     on public.snapshots;
drop policy if exists "snapshots: créer ses lignes"    on public.snapshots;
drop policy if exists "snapshots: modifier ses lignes" on public.snapshots;

create policy "snapshots: lire ses lignes" on public.snapshots
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "snapshots: créer ses lignes" on public.snapshots
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "snapshots: modifier ses lignes" on public.snapshots
  for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.snapshots from anon;
grant select, insert, update on public.snapshots to authenticated;

-- ---------------------------------------------------------------------------
-- Commandes envoyées depuis l'iPhone (ADR 021). Mode PAPER uniquement.
-- L'app DÉPOSE une demande ; le Mac (scripts/publie_supabase.py, toutes les
-- 2 min) la relit, la VALIDE avec les mêmes règles que le site, l'applique et
-- écrit le résultat. Rien ici ne peut passer d'ordre réel : le bot n'a pas de
-- module d'exécution. Une demande de plus de 15 min est refusée (« expirée ») :
-- un /STOP ne doit pas partir des heures plus tard au réveil du Mac.
--   stop       : arrêt d'urgence (ferme toutes les positions papier, met en pause)
--   resume     : reprise des entrées
--   pm_toggle  : activer / désactiver un bras Liquid    payload {arm, enabled}
--   pm_fork    : créer un bras Liquid (une variable)     payload {parent, name, variable, value}

create table if not exists public.commandes (
  id         bigint      generated always as identity primary key,
  user_id    uuid        not null default auth.uid() references auth.users (id) on delete cascade,
  action     text        not null check (action in ('stop', 'resume', 'pm_toggle', 'pm_fork')),
  payload    jsonb       not null default '{}'::jsonb check (pg_column_size(payload) < 2000),
  cree_a     timestamptz not null default now(),
  statut     text        not null default 'en attente' check (statut in ('en attente', 'appliquée', 'refusée')),
  detail     text        check (detail is null or char_length(detail) <= 300),
  traite_a   timestamptz
);
create index if not exists commandes_attente on public.commandes (user_id, statut, id);

alter table public.commandes enable row level security;

drop policy if exists "commandes: lire ses lignes"     on public.commandes;
drop policy if exists "commandes: créer ses lignes"    on public.commandes;
drop policy if exists "commandes: traiter ses lignes"  on public.commandes;

create policy "commandes: lire ses lignes" on public.commandes
  for select to authenticated using ((select auth.uid()) = user_id);
-- Une demande naît « en attente », sans résultat : l'app ne peut pas s'écrire un « appliquée ».
create policy "commandes: créer ses lignes" on public.commandes
  for insert to authenticated
  with check ((select auth.uid()) = user_id and statut = 'en attente' and detail is null and traite_a is null);
create policy "commandes: traiter ses lignes" on public.commandes
  for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

revoke all on public.commandes from anon;
grant select, insert, update on public.commandes to authenticated;
