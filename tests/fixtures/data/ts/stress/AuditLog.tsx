import { useState } from "react";

type Role = "admin" | "member" | "viewer";
type User = {
  id: string;
  name: string;
  email: string;
  bytes: number;
  active: boolean;
  role: Role;
};

interface AuditRowProps {
  user: User;
  selected: boolean;
  density: "compact" | "cozy";
  showEmail: boolean;
  highlight: string;
  onSelect: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

// A THIRD copy of the same row — the duplication now spans AdminConsole, ReportsView, and
// AuditLog, so the cross-file finding should name every other site.
export function AuditRow({
  user,
  selected,
  density,
  showEmail,
  highlight,
  onSelect,
  onEdit,
  onDelete,
}: AuditRowProps) {
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

// A THIRD copy of the byte formatter.
function humanSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1000));
  return `${(bytes / Math.pow(1000, i)).toFixed(1)} ${units[i]}`;
}

export function AuditLog() {
  const [entries] = useState<User[]>([]);
  return (
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
        {entries.map((u) => (
          <AuditRow
            key={u.id}
            user={u}
            selected={false}
            density="cozy"
            showEmail
            highlight=""
            onSelect={() => undefined}
            onEdit={() => undefined}
            onDelete={() => undefined}
          />
        ))}
      </tbody>
    </table>
  );
}
