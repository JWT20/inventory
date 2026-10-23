export function BrandFooter({ className = "" }: { className?: string }) {
  return (
    <p className={`text-xs text-muted-foreground text-center ${className}`}>
      Dockscan — © 2026{" "}
      <a
        href="https://tyneworks.nl"
        target="_blank"
        rel="noopener noreferrer"
        className="underline-offset-2 hover:underline hover:text-foreground"
      >
        Tyne Works
      </a>
    </p>
  );
}
