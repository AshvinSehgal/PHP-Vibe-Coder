# PHP Vibe Coder coding standards

Use these rules when planning, generating, reviewing, or repairing application code.

- Write small CodeIgniter 4 controllers that coordinate requests and delegate database work to models.
- Put controllers in `app/Controllers`, models in `app/Models`, views in `app/Views`, migrations in `app/Database/Migrations`, and public assets in `public/`.
- Use clear names and short functions. Avoid unexplained abbreviations.
- Keep generated PHP beginner-friendly and omit PHP parameter, property, and return type declarations.
- Escape displayed values with CodeIgniter's `esc()` helper.
- Validate user input before saving it and use CodeIgniter models or the query builder instead of joining SQL strings.
- Store passwords with PHP password hashing functions and never display or log secrets.
- Keep CSS in `public/css/app.css` and JavaScript in `public/js/app.js`. Avoid inline styles, inline scripts, CDNs, and unnecessary packages.
- Use semantic HTML, visible labels, keyboard focus styles, responsive layouts, and readable color contrast.
- Keep credentials in `.env`, which must not be committed.
- Prefer the simplest design that satisfies the prompt. Do not add authentication, administration, uploads, or other sensitive features unless requested.

