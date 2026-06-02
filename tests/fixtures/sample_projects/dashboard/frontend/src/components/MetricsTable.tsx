type Row = { id: string; name: string };

export function MetricsTable({ rows, html }: { rows: Row[]; html: string }) {
  return (
    <table>
      <tbody>
        <tr onMouseOver={() => preview(html)}>
          <td tabIndex={3} onClick={() => select()}>
            {rows.length}
          </td>
          <td>
            <img src="/sparkline.png" />
          </td>
          <td>
            <button>
              <TrashIcon />
            </button>
          </td>
          <td>
            <TrashIcon /> Remove
          </td>
          <td>
            <a href="javascript:void(0)">run</a>
          </td>
          <td>
            <a href="/export" target="_blank">
              export
            </a>
          </td>
          <td dangerouslySetInnerHTML={{ __html: html }} />
          <td>
            <input autoFocus placeholder="filter" />
          </td>
          <td>
            <nav role="navigation">
              <iframe src="/embed" />
            </nav>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

function preview(html: string) {
  return eval(html);
}

function select() {
  return null;
}
