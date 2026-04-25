interface PlaceholderPageProps {
  title: string;
}

export function PlaceholderPage({ title }: PlaceholderPageProps) {
  return (
    <div>
      <h1 className="text-xl font-bold text-text-primary mb-4">{title}</h1>
      <div className="bg-bg-surface border border-border rounded-lg p-8 text-center">
        <p className="text-text-muted">This feature is under development.</p>
      </div>
    </div>
  );
}
