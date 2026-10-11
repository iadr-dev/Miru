"use client";

import type { ReactNode } from "react";
import { DeleteAccount } from "./delete-account";
import { ProfileForm } from "./profile-form";

/** The Profile settings panel: the details form, then the delete-account danger zone. Shared by
 * the full-page route and the settings dialog so both show the same thing. */
export function ProfilePanel(): ReactNode {
  return (
    <div className="space-y-4">
      <ProfileForm />
      <DeleteAccount />
    </div>
  );
}
