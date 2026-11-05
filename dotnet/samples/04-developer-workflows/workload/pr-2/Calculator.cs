internal static class Calculator
{
    public static int Add(int a, int b) => checked(a + b);
    public static int Subtract(int a, int b) => checked(a - b);
    public static int Multiply(int a, int b) => checked(a * b);
}
