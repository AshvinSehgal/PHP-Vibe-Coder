# Beginner-friendly PHP and CodeIgniter design patterns

## Model-View-Controller

Use CodeIgniter's MVC structure as the default. A route selects a controller method. The controller reads and validates request data, calls a model, and passes plain data to a view. The view renders HTML and does not perform database queries.

## Repository-like model boundary

For small generated applications, a CodeIgniter model is enough as the database boundary. Put table names, allowed fields, validation rules, and simple queries in the model. Do not create an extra repository class unless the application is genuinely complex.

## Service layer when needed

If business logic is reused by several controllers, place that logic in a small service class. Controllers should still handle HTTP concerns and models should still handle persistence. A one-page CRUD application normally does not need a service layer.

## Dependency creation

Use CodeIgniter services or create a model near where it is used. Avoid global mutable state. For a beginner project, straightforward construction is preferable to a complicated dependency-injection container.

## Validation and errors

Treat validation as a boundary before persistence. Return the form with understandable validation messages for expected input errors. Log unexpected server errors without exposing stack traces or credentials to users.

## Post/Redirect/Get

After a successful form submission, redirect to a GET page. This prevents accidental resubmission when the browser refreshes.

## Database migration pattern

Represent schema changes as reversible migrations with complete `up()` and `down()` methods. Add a primary key and indexes for fields used frequently in lookups or relationships.

