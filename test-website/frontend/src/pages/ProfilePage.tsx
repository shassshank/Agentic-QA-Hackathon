import { api } from "../api/client";
import { ErrorState, LoadingState } from "../components/Status";
import { useApiData } from "../lib/useApiData";

export function ProfilePage() {
  const result = useApiData((signal) => api.getMe(signal), []);

  if (result.status === "loading") return <LoadingState label="Loading profile…" />;
  if (result.status === "error") {
    return <ErrorState title="We couldn't load your profile" error={result.error} onRetry={result.reload} />;
  }

  const me = result.data;
  return (
    <>
      <div className="page-header">
        <div>
          <p className="eyebrow">Profile</p>
          <h1>Your profile</h1>
        </div>
      </div>
      <section className="panel" aria-labelledby="profile-heading">
        <h2 id="profile-heading" className="section-title">
          Demo customer details
        </h2>
        <dl className="detail-list">
          <div>
            <dt>Display name</dt>
            <dd data-testid="profile-name">{me.display_name}</dd>
          </div>
          <div>
            <dt>Email</dt>
            <dd data-testid="profile-email">{me.masked_email}</dd>
          </div>
        </dl>
        <p className="muted">These are synthetic demo details. DemoBank never stores real identity information.</p>
      </section>
    </>
  );
}
