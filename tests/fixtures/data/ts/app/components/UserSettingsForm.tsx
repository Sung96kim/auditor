export function UserSettingsForm() {
  return (
    <form>
      <input type="text" autoFocus />
      <select>
        <option>UTC</option>
      </select>
      <a onClick={() => undefined}>Reset to defaults</a>
      <button role="button">Save</button>
      <div onMouseOver={() => undefined}>Hover for help</div>
    </form>
  );
}
