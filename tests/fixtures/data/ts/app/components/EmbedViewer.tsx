export function EmbedViewer({ html, src }: { html: string; src: string }) {
  return (
    <div>
      <div dangerouslySetInnerHTML={{ __html: html }} />
      <img src={src} />
      <iframe src={src} />
      <a href="/docs" target="_blank">
        Documentation
      </a>
      <a href="javascript:void(0)">Run action</a>
      <button>
        <TrashIcon /> Delete
      </button>
    </div>
  );
}
