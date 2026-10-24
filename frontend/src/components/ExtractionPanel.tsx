import { fieldLabel } from "../lib/decision";
import { money, show } from "../lib/format";
import type { Extraction } from "../types";

const ORDER = [
  "claimant_name",
  "policy_number",
  "incident_date",
  "reported_date",
  "incident_type",
  "incident_location",
  "vehicle_year",
  "vehicle_make",
  "vehicle_model",
  "police_report_number",
  "total_amount",
];

interface Item {
  description: string;
  amount: string;
}

export default function ExtractionPanel({ extraction }: { extraction: Extraction }) {
  const { fields, meta } = extraction;
  const vision = new Set(meta.vision_sourced ?? []);
  const unverified = new Set(meta.unverified_fields ?? []);
  const items = (fields.itemized_damages as Item[] | undefined) ?? [];
  const account = fields.incident_description as string | null | undefined;

  return (
    <section className="card" aria-labelledby="ext-h">
      <h2 id="ext-h">Extracted fields</h2>
      {meta.needs_review && (
        <p className="warn" role="note">
          Extraction did not pass its checks
          {meta.issues && meta.issues.length > 0 ? `: ${meta.issues.join("; ")}` : ""}.
        </p>
      )}
      <dl className="fields">
        {ORDER.map((name) => (
          <div key={name}>
            <dt>{fieldLabel(name)}</dt>
            <dd>
              {name === "total_amount" ? money(fields[name]) : show(fields[name])}
              {vision.has(name) && (
                <span className="tag scan" title="Read from a scanned page; confirm against the image">
                  read from scan
                </span>
              )}
              {unverified.has(name) && (
                <span className="tag warn-tag" title="The quoted evidence was not found in the documents">
                  unverified
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {items.length > 0 && (
        <table className="items" aria-label="Itemised damages">
          <tbody>
            {items.map((it, i) => (
              <tr key={i}>
                <td>{it.description}</td>
                <td className="num">{money(it.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {account && (
        <>
          <h3>Claimant&rsquo;s account</h3>
          <p className="account">{account}</p>
        </>
      )}
    </section>
  );
}
