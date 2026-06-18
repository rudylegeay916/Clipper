import * as React from "react";
import Link from "next/link";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-volt disabled:pointer-events-none disabled:opacity-50 active:scale-[0.98] whitespace-nowrap",
  {
    variants: {
      variant: {
        primary:
          "bg-volt text-volt-foreground hover:bg-volt-strong shadow-[0_8px_30px_-8px_rgba(198,242,78,0.5)]",
        secondary:
          "bg-surface-2 text-fg border border-border-strong hover:bg-surface-3",
        ghost: "text-fg hover:bg-surface-2",
        outline: "border border-border-strong text-fg hover:bg-surface-2",
      },
      size: {
        sm: "h-9 px-3 text-sm",
        md: "h-11 px-5 text-[15px]",
        lg: "h-13 px-7 text-base",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type ButtonBaseProps = VariantProps<typeof buttonVariants> & {
  className?: string;
};

export function Button({
  className,
  variant,
  size,
  ...props
}: ButtonBaseProps & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}

export function ButtonLink({
  className,
  variant,
  size,
  href,
  ...props
}: ButtonBaseProps & React.ComponentProps<typeof Link>) {
  return (
    <Link className={cn(buttonVariants({ variant, size }), className)} href={href} {...props} />
  );
}

export { buttonVariants };
