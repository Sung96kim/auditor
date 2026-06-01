export function SummaryPanel() {
  return (
    <>
      <h3>Summary</h3>
      <p>Totals across the selected range.</p>
      <table className="data">
        <thead>
          <tr>
            <th>Name</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          <tr><td>Total</td><td>120</td></tr>
          <tr><td>Average</td><td>40</td></tr>
          <tr><td>Peak</td><td>80</td></tr>
        </tbody>
      </table>
    </>
  );
}
