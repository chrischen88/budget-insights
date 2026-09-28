-- Fallback mapping from Chase's coarse card categories to our taxonomy (SPEC.md §5.5 step 6).
-- Editable later from Settings. Chase categories with no row here stay uncategorized.

CREATE TABLE chase_category_mappings (
    chase_category TEXT PRIMARY KEY,
    category_id    INTEGER NOT NULL REFERENCES categories(id)
);

INSERT INTO chase_category_mappings (chase_category, category_id)
SELECT m.chase_category, c.id
FROM (VALUES
    ('Automotive',            'Transportation'),
    ('Bills & Utilities',     'Bills & Utilities'),
    ('Education',             'Education'),
    ('Entertainment',         'Entertainment'),
    ('Fees & Adjustments',    'Fees & Charges'),
    ('Food & Drink',          'Food & Dining'),
    ('Gas',                   'Gas & Fuel'),
    ('Gifts & Donations',     'Gifts & Donations'),
    ('Groceries',             'Groceries'),
    ('Health & Wellness',     'Health & Wellness'),
    ('Home',                  'Housing'),
    ('Personal',              'Personal Care'),
    ('Shopping',              'Shopping'),
    ('Travel',                'Travel')
) AS m(chase_category, category_name)
JOIN categories c ON c.name = m.category_name;
