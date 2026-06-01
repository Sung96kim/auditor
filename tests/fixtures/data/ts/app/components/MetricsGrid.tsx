export function MetricsGrid() {
  return (
    <>
      <h2>Live metrics</h2>
      <table className="data">
        <thead>
          <tr>
            <th>Name</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          <tr><td>CPU</td><td>40%</td></tr>
          <tr><td>Memory</td><td>60%</td></tr>
          <tr><td>Disk</td><td>20%</td></tr>
        </tbody>
      </table>
    </>
  );
}
