import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useReducer } from "react";
import { SearchIcon, TrashIcon, EditIcon, CloseIcon } from "@/components/ui/icons";

import { fetchUsers, deleteUser } from "../lib/api";
import { formatBytes } from "../lib/format";

type Role = "admin" | "member" | "viewer";
type User = {
  id: string;
  name: string;
  email: string;
  bytes: number;
  active: boolean;
  role: Role;
};

type SortKey = "name" | "bytes" | "role";

interface FilterState {
  query: string;
  role: Role | "all";
  sort: SortKey;
  desc: boolean;
}

function filterReducer(state: FilterState, patch: Partial<FilterState>): FilterState {
  return { ...state, ...patch };
}

// near-twin helpers differing only in a constant — should be one parameterized function
function toKb(n: number): string {
  const v = n / 1000;
  return v.toFixed(1);
}
function toMb(n: number): string {
  const v = n / 1000000;
  return v.toFixed(1);
}

// a verbatim re-implementation of lib/format.formatBytes — should import the shared util
function humanSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1000));
  return `${(bytes / Math.pow(1000, i)).toFixed(1)} ${units[i]}`;
}

function evalSort(expr: string): number {
  return eval(expr);
}

interface UserRowProps {
  user: User;
  selected: boolean;
  density: "compact" | "cozy";
  showEmail: boolean;
  highlight: string;
  onSelect: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

export function UserRow({
  user,
  selected,
  density,
  showEmail,
  highlight,
  onSelect,
  onEdit,
  onDelete,
}: UserRowProps) {
  return (
    <tr data-density={density} aria-selected={selected}>
      <td onClick={() => onSelect(user.id)}>
        <span className="rounded-full bg-emerald-500/10 px-2 text-emerald-600">
          {user.active ? "active" : "idle"}
        </span>
      </td>
      <td>{highlight ? user.name.replace(highlight, "*") : user.name}</td>
      {showEmail ? <td>{user.email}</td> : null}
      <td>{humanSize(user.bytes)}</td>
      <td>
        <button onClick={() => onEdit(user.id)}>
          <EditIcon />
        </button>
        <button onClick={() => onDelete(user.id)}>
          <TrashIcon />
        </button>
      </td>
    </tr>
  );
}

function Toolbar({ filter, dispatch }: { filter: FilterState; dispatch: (p: Partial<FilterState>) => void }) {
  return (
    <div className="toolbar" onMouseOver={() => undefined}>
      <input autoFocus value={filter.query} onChange={(e) => dispatch({ query: e.target.value })} />
      <button role="button" onClick={() => dispatch({ desc: !filter.desc })}>
        <SearchIcon />
      </button>
      <div onClick={() => dispatch({ sort: "name" })} tabIndex={3}>
        Sort by name
      </div>
      <a onClick={() => dispatch({ role: "all" })}>Clear</a>
      <a href="javascript:window.print()">Print</a>
    </div>
  );
}

export function AdminConsole() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [notes, setNotes] = useState("<b>welcome</b>");
  const [filter, dispatch] = useReducer(filterReducer, {
    query: "",
    role: "all",
    sort: "name",
    desc: false,
  });
  const mounted = useRef(true);

  useEffect(() => {
    fetchUsers()
      .then(setUsers)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
    return () => {
      mounted.current = false;
    };
  }, []);

  const visible = useMemo(() => {
    const matched = users.filter(
      (u) =>
        u.name.includes(filter.query) && (filter.role === "all" || u.role === filter.role),
    );
    const sorted = [...matched].sort((a, b) =>
      filter.sort === "bytes" ? a.bytes - b.bytes : a.name.localeCompare(b.name),
    );
    return filter.desc ? sorted.reverse() : sorted;
  }, [users, filter]);

  const onDelete = useCallback(
    (id: string) => deleteUser(id).then(() => setUsers((prev) => prev.filter((u) => u.id !== id))),
    [],
  );

  // pure helper closing over no component state — belongs in lib/
  function summarize(list: User[]): string {
    const totalBytes = list.reduce((acc, u) => acc + u.bytes, 0);
    return `${list.length} users · ${formatBytes(totalBytes)}`;
  }

  if (error) {
    return <div role="alert">Failed: {error}</div>;
  }

  return (
    <main className="admin">
      <header>
        <h1>Admin</h1>
        <div className="stats">
          <div className="tile"><span>Users</span><strong>{users.length}</strong></div>
          <div className="tile"><span>Active</span><strong>{users.filter((u) => u.active).length}</strong></div>
          <div className="tile"><span>Storage</span><strong>{summarize(visible)}</strong></div>
        </div>
      </header>

      <Toolbar filter={filter} dispatch={dispatch} />

      <section>
        <div>
          <div>
            <div>
              <div>
                <div>
                  <table>
                    <thead>
                      <tr>
                        <th>Status</th>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Size</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((u) => (
                        <UserRow
                          key={u.id}
                          user={u}
                          selected={u.id === selected}
                          density="cozy"
                          showEmail
                          highlight={filter.query}
                          onSelect={setSelected}
                          onEdit={() => setDrawer(true)}
                          onDelete={onDelete}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside dangerouslySetInnerHTML={{ __html: notes }} />

      <iframe src="/analytics/embed" />

      <footer>
        <img src="/logo.png" />
        <a href="/help" target="_blank">
          Help
        </a>
        <Button className="h-7 w-7" onClick={() => setDrawer(false)}>
          <CloseIcon />
        </Button>
      </footer>
    </main>
  );
}
