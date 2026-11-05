var checks = new (string Name, int Expected, int Actual)[]
{
    ("Add", 7, Calculator.Add(3, 4)),
    ("Subtract", -1, Calculator.Subtract(3, 4)),
    ("Multiply", 12, Calculator.Multiply(3, 4)),
    ("MultiplyByZero", 0, Calculator.Multiply(9, 0))
};

foreach (var check in checks)
{
    Console.WriteLine($"{check.Name}: expected {check.Expected}, actual {check.Actual}");
}

return checks.All(check => check.Expected == check.Actual) ? 0 : 2;
