-------------------------------- MODULE MC --------------------------------
(* Request mixes the .cfg files bind to Route and Key. *)
EXTENDS ChatQuota, TLC

\* Three chat sends; 1 and 2 carry the same Idempotency-Key (a double-click or a retry).
RouteQuery3 == [r \in 1..3 |-> "query"]
KeyDup3     == (1 :> "k1" @@ 2 :> "k1" @@ 3 :> "none")

\* An edit-resend and a regenerate.
RouteEditRegen == (1 :> "edit" @@ 2 :> "regenerate")

\* One scheduled job of the user and one chat send.
RouteSched == (1 :> "scheduled" @@ 2 :> "query")

KeyNone2 == [r \in 1..2 |-> "none"]

\* The three other routes together.
RouteOther3 == (1 :> "edit" @@ 2 :> "regenerate" @@ 3 :> "scheduled")
KeyNone3    == [r \in 1..3 |-> "none"]
=============================================================================
