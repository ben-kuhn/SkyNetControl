export function PrivacyPolicyPage() {
  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-2xl font-bold text-text-primary mb-6">
        Privacy Policy
      </h1>

      <div className="bg-bg-surface border border-border rounded-lg p-6 space-y-6 text-sm text-text-secondary leading-relaxed">
        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            What Data We Collect
          </h2>
          <p>
            This application collects the following data to operate the amateur
            radio net:
          </p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>
              <strong>Callsign</strong> &mdash; your FCC-assigned amateur radio
              callsign, used as your account identifier
            </li>
            <li>
              <strong>Name</strong> &mdash; your name as provided during
              registration
            </li>
            <li>
              <strong>Email</strong> &mdash; optional, used for account recovery
            </li>
            <li>
              <strong>Location</strong> &mdash; city, county, state, and
              coordinates submitted with check-ins
            </li>
            <li>
              <strong>Check-in messages</strong> &mdash; messages received via
              Winlink or entered manually
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Why We Collect It
          </h2>
          <p>Data is collected for:</p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>Net operations &mdash; tracking check-ins and participation</li>
            <li>
              Roster generation &mdash; producing net rosters for distribution
            </li>
            <li>
              Check-in tracking &mdash; maintaining member activity history
            </li>
          </ul>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            How Data Is Stored
          </h2>
          <p>
            All data is stored in a local database on the net operator's server.
            This is a self-hosted application &mdash; your data stays on the
            server operated by your net control.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            External Sharing
          </h2>
          <p>
            When delivery backends are configured, net content (reminders and
            rosters) may be shared via:
          </p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>groups.io &mdash; posted to the configured group</li>
            <li>Email &mdash; sent to the configured address via SMTP</li>
            <li>Winlink &mdash; sent via PAT radio email</li>
          </ul>
          <p className="mt-2">
            Individual user data is not shared externally unless it appears in a
            roster or check-in summary.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Cookies
          </h2>
          <p>
            This application uses two strictly-necessary HTTPOnly cookies for
            authentication (<code>access_token</code> and{" "}
            <code>refresh_token</code>). No tracking, analytics, or third-party
            cookies are used.
          </p>
        </section>

        <section>
          <h2 className="text-lg font-semibold text-text-primary mb-2">
            Your Rights
          </h2>
          <p>You have the right to:</p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>
              <strong>Export your data</strong> &mdash; download all data
              associated with your account as a JSON file
            </li>
            <li>
              <strong>Delete your account</strong> &mdash; anonymize your
              account, replacing all personal data with opaque placeholders
            </li>
          </ul>
          <p className="mt-2">
            Both actions are available from your{" "}
            <a href="/profile" className="text-accent hover:underline">
              Profile page
            </a>
            . Net administrators can also perform these actions on your behalf.
          </p>
        </section>
      </div>
    </div>
  );
}
