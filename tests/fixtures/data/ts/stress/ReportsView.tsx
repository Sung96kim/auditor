import { useState } from "react";

import { fetchUsers } from "../lib/api";

type Role = "admin" | "member" | "viewer";
type User = {
  id: string;
  name: string;
  email: string;
  bytes: number;
  active: boolean;
  role: Role;
};

interface ReportRowProps {
  user: User;
  selected: boolean;
  density: "compact" | "cozy";
  showEmail: boolean;
  highlight: string;
  onSelect: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
}

// Structurally identical to AdminConsole.UserRow — the two have drifted into parallel row
// implementations that should be one shared component.
export function ReportRow({
  user,
  selected,
  density,
  showEmail,
  highlight,
  onSelect,
  onEdit,
  onDelete,
}: ReportRowProps) {
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

// A verbatim copy of AdminConsole.humanSize / lib/format.formatBytes.
function humanSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1000));
  return `${(bytes / Math.pow(1000, i)).toFixed(1)} ${units[i]}`;
}

export function ReportsView() {
  const [users] = useState<User[]>([]);
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
        {users.map((u) => (
          <ReportRow
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
