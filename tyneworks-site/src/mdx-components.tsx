import type { MDXComponents } from "mdx/types";

export function useMDXComponents(components: MDXComponents): MDXComponents {
  return {
    ...components,
    h2: (props) => <h2 className="text-2xl mt-10 mb-4" {...props} />,
    h3: (props) => <h3 className="text-xl mt-8 mb-3" {...props} />,
    p: (props) => <p className="my-4 text-ink/85 leading-relaxed" {...props} />,
    ul: (props) => <ul className="list-disc pl-6 my-4 space-y-1 text-ink/85" {...props} />,
    ol: (props) => <ol className="list-decimal pl-6 my-4 space-y-1 text-ink/85" {...props} />,
    a: (props) => <a className="text-brand-700 underline hover:no-underline" {...props} />,
  };
}
