// Shared tests: run on every platform build (Windows, Mac, Linux, iOS/Android simulators, wasm via node).
#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include "doctest/doctest.h"
#include "text.h"

using tiro::FormatOptions;
using tiro::TextAssembler;

TEST_CASE("a single phrase keeps its final full stop until the dictation ends") {
  TextAssembler a(FormatOptions{});
  CHECK(a.add_phrase("Hello there, how are you?", 0) == "Hello there, how are you");
  CHECK(a.finish() == "?");
  CHECK(a.text() == "Hello there, how are you?");
}

TEST_CASE("a pause mid-sentence does not end the sentence") {
  TextAssembler a(FormatOptions{});
  CHECK(a.add_phrase("I went to the shop.", 0) == "I went to the shop");
  // short pause, then a continuation word: the recogniser's full stop and capital are both wrong
  CHECK(a.add_phrase("And bought some milk.", 500) == " and bought some milk");
  CHECK(a.finish() == ".");
  CHECK(a.text() == "I went to the shop and bought some milk.");
}

TEST_CASE("a new sentence after a pause keeps the full stop and the capital") {
  TextAssembler a(FormatOptions{});
  a.add_phrase("That's done.", 0);
  CHECK(a.add_phrase("Next we need the slides.", 2000) == ". Next we need the slides");
  a.finish();
  CHECK(a.text() == "That's done. Next we need the slides.");
}

TEST_CASE("a question mark is kept and the next sentence capitalised") {
  TextAssembler a(FormatOptions{});
  a.add_phrase("Is it ready?", 0);
  CHECK(a.add_phrase("Or should I wait?", 300) == "? Or should I wait");
}

TEST_CASE("fillers are removed") {
  TextAssembler a(FormatOptions{});
  CHECK(a.add_phrase("So um I think uh we should go.", 0) == "So I think we should go");
}

TEST_CASE("voice commands become line breaks") {
  TextAssembler a(FormatOptions{});
  a.add_phrase("Dear Sam,", 0);
  CHECK(a.add_phrase("New paragraph.", 900) == "\n\n");
  CHECK(a.add_phrase("thanks for the update.", 900) == "Thanks for the update");
  a.finish();
  CHECK(a.text() == "Dear Sam,\n\nThanks for the update.");
}

TEST_CASE("continuing a sentence the user was typing") {
  TextAssembler a(FormatOptions{}, /*needs_space=*/true, /*mid_sentence=*/true);
  CHECK(a.add_phrase("The meeting is at three.", 0) == " the meeting is at three");
}

TEST_CASE("names, acronyms and mixed case are not lowercased") {
  TextAssembler a(FormatOptions{}, true, true);
  CHECK(a.add_phrase("NASA launched it.", 0) == " NASA launched it");
  TextAssembler b(FormatOptions{});
  b.add_phrase("I bought an iPhone.", 0);
  CHECK(b.add_phrase("iPhones are great.", 3000) == ". iPhones are great");
}

TEST_CASE("mister becomes Mr. before a name") {
  TextAssembler a(FormatOptions{});
  CHECK(a.add_phrase("I met mister Smith today.", 0) == "I met Mr. Smith today");
}
