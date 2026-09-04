-- Mark solo games, so balance figures can leave them out.
--
-- One person playing both sides is not evidence about which civilization is
-- stronger, and until now nothing in the row could tell such a game apart from
-- an ordinary one between two anonymous players: both have null user ids on
-- each seat.
alter table games add column solo boolean not null default false;

-- The balance query reads civ, tempo and result for real two-sided games.
create index games_balance_real on games (civ_table, tempo) where not solo;
