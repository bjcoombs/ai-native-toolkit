defmodule Example.Greeter do
  @moduledoc """
  Builds greetings.

  ## Examples

      iex> Example.Greeter.hello("Ada")
      "Hello, Ada"
  """

  @typedoc ~S"""
  A name to greet.
  """
  @type name :: String.t()

  @doc """
  Greets `name`.
  """
  @spec hello(name()) :: String.t()
  def hello(name), do: "Hello, " <> name

  @doc false
  def internal, do: :ok

  @doc "One-line docs stay in the count."
  def shout(name), do: String.upcase(hello(name))
end
