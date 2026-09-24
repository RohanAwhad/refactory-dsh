public class Sum {
    public static void main(String[] args) {
        int total = 0;
        for (String arg : args) {
            total += Integer.parseInt(arg);
        }
        System.out.println("sum=" + total);
    }
}
