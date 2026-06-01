import { SearchIcon, FilterIcon } from "../lib/icons";

export function Toolbar({ onSearch }: { onSearch: () => void }) {
  return (
    <div role="toolbar">
      <button onClick={onSearch}>
        <SearchIcon />
      </button>
      <button onClick={onSearch}>
        <FilterIcon />
      </button>
      <div onClick={onSearch} tabIndex={2}>
        Filters
      </div>
    </div>
  );
}
