import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold uppercase",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground",
        pending: "bg-amber-500 text-black",
        picking: "bg-violet-500 text-white",
        receiving: "bg-blue-500 text-white",
        completed: "bg-green-600 text-white",
        fulfilled: "bg-green-600 text-white",
        active: "bg-green-600 text-white",
        inactive: "bg-amber-500 text-black",
        secondary: "bg-secondary text-secondary-foreground",
        destructive: "bg-destructive text-destructive-foreground",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
